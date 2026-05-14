"""
.env file read / write / mask helpers for the UI.

Design rules (per project plan, §5 Risk R4 — secret handling, and R10 —
preserve hand-edited keys):

1. **Round-trip safe.** ``write_env()`` parses the existing file, updates
   only the keys the UI manages, and preserves every other line — comments,
   blank lines, ordering, and unknown keys all survive. The user can hand-edit
   `.env` and still use the UI without losing their edits.

2. **Secrets are masked on read, never on write.** ``read_env(mask=True)``
   returns the secret keys as ``"****XXXX"`` (last 4 chars) so the UI form
   can show them without leaking. ``write_env()`` requires the caller to
   pass the full value or the sentinel ``KEEP_EXISTING`` for masked fields
   — never write the masked string back to disk.

3. **File mode 0o600.** Always. The UI binds to 127.0.0.1, but defense in
   depth: if another local user reads the file they shouldn't get the keys.

4. **.gitignore guard.** Before writing, verify that the `.env` filename
   pattern is covered by `.gitignore` in the same repo root. Refuse to write
   if it isn't — that's how secrets accidentally land in git history.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Sentinel that callers pass in `write_env(values)` for keys whose value
# was masked on read and should be left untouched on write. Using a
# distinct sentinel (rather than e.g. an empty string) prevents the
# "user cleared the field by accident → secret deleted" footgun.
KEEP_EXISTING = "__DD_DEMO_UI_KEEP_EXISTING__"

# Keys the UI considers secret. ``read_env(mask=True)`` will mask these;
# writing them with ``KEEP_EXISTING`` preserves the on-disk value.
SECRET_KEYS = frozenset({"DD_API_KEY", "DD_APP_KEY"})

# Keys the UI form manages. ``write_env()`` only touches these; everything
# else in the file is preserved verbatim. Adding a knob to the UI means
# adding it here AND surfacing it in the form.
MANAGED_KEYS = frozenset({
    "DD_API_KEY",
    "DD_APP_KEY",
    "DD_SITE",
    "DD_DEMO_VERTICAL",
    "DD_DEMO_SUB_VERTICAL",
    "EMIT_INTERVAL",
    "DISPLAY_NAME",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
})

# Match `KEY=value` lines with optional whitespace and optional inline
# quoting. The dotenv spec allows quoted values containing `=`, so we
# only split on the FIRST `=`. Lines that don't match are preserved verbatim
# (comments, blank lines, line continuations, etc.).
_ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


@dataclass
class EnvFile:
    """Parsed `.env` representation that preserves the raw lines.

    `lines` is the verbatim source split on '\n' (no trailing newline kept
    on individual entries). `values` maps key → current value. The two stay
    in sync: writing a new value updates the line in-place, or appends a new
    line if the key wasn't present.
    """
    path: Path
    lines: List[str]
    values: Dict[str, str]


def mask_secret(value: str) -> str:
    """Mask a secret for display: show only the last 4 chars."""
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return "*" * (len(value) - 4) + value[-4:]


def parse_env(path: Path) -> EnvFile:
    """Parse a `.env` file into an EnvFile.

    Missing file → returns an empty EnvFile (path set, no lines, no values).
    This lets the UI render an empty form on first run.
    """
    if not path.exists():
        return EnvFile(path=path, lines=[], values={})

    raw = path.read_text(encoding="utf-8")
    # Strip a trailing newline so we don't accumulate a blank last line
    # on every round-trip. We re-add it on write.
    if raw.endswith("\n"):
        raw = raw[:-1]
    lines = raw.split("\n") if raw else []

    values: Dict[str, str] = {}
    for line in lines:
        m = _ENV_LINE_RE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        # Strip surrounding quotes from the value, matching python-dotenv's
        # behaviour. We re-quote on write only if the value contains
        # whitespace or special chars.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value

    return EnvFile(path=path, lines=lines, values=values)


def read_env(path: Path, mask: bool = True) -> Dict[str, str]:
    """Return a flat key → value dict.

    With ``mask=True`` (default), secrets are masked. The UI's GET handler
    should use mask=True so the response is safe to put into the browser.
    Internal callers that need the real value (e.g. for connection-test)
    pass mask=False.
    """
    env = parse_env(path)
    out: Dict[str, str] = {}
    for k, v in env.values.items():
        if mask and k in SECRET_KEYS:
            out[k] = mask_secret(v)
        else:
            out[k] = v
    return out


def _format_value(value: str) -> str:
    """Render a value for the right-hand side of `KEY=value`.

    Quoting rules (matching python-dotenv's expectations):
    - Empty string → ``""``
    - Contains whitespace, ``#``, ``"``, or ``'`` → double-quoted with
      embedded double quotes escaped.
    - Otherwise → bare.
    """
    if value == "":
        return '""'
    needs_quoting = any(c in value for c in (" ", "\t", "#", '"', "'"))
    if needs_quoting:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def write_env(
    path: Path,
    new_values: Dict[str, str],
    *,
    require_gitignore: bool = True,
) -> EnvFile:
    """Write `new_values` to `path`, preserving unknown keys and structure.

    For each key in ``new_values``:
      - If value is ``KEEP_EXISTING``, the on-disk value is left untouched.
      - Otherwise, the corresponding line is rewritten (or appended if
        the key wasn't present).

    Comments, blank lines, and keys not in ``new_values`` are preserved
    verbatim. The file is written atomically (write to `.env.tmp`, fsync,
    rename) at mode 0o600.

    Raises:
        ValueError: if ``require_gitignore`` and `.env` is not gitignored.
        ValueError: if a key in ``new_values`` is not in MANAGED_KEYS.
            (Prevents the UI from clobbering keys it shouldn't touch.)
    """
    # Reject keys the UI shouldn't be writing. Anything outside MANAGED_KEYS
    # is either a hand-edited custom var (keep it) or a typo (don't propagate).
    unmanaged = set(new_values.keys()) - MANAGED_KEYS
    if unmanaged:
        raise ValueError(
            f"write_env() refusing to write unmanaged keys: {sorted(unmanaged)}. "
            f"Add them to env_manager.MANAGED_KEYS if the UI now manages them."
        )

    if require_gitignore:
        _assert_env_is_gitignored(path)

    env = parse_env(path)
    written_keys: set = set()

    # First pass: rewrite existing lines in-place.
    new_lines: List[str] = []
    for line in env.lines:
        m = _ENV_LINE_RE.match(line)
        if not m:
            new_lines.append(line)
            continue
        key = m.group(1)
        if key not in new_values:
            new_lines.append(line)
            continue

        incoming = new_values[key]
        if incoming == KEEP_EXISTING:
            # Preserve the on-disk line verbatim (don't reformat quoting).
            new_lines.append(line)
            written_keys.add(key)
            continue

        new_lines.append(f"{key}={_format_value(incoming)}")
        written_keys.add(key)

    # Second pass: append new keys that weren't already in the file. They
    # land at the end as one tight block, separated from any preceding
    # non-blank content by a single blank line — never separated from each
    # other, so the file stays compact.
    pending_appends: List[Tuple[str, str]] = []
    for key, incoming in new_values.items():
        if key in written_keys:
            continue
        if incoming == KEEP_EXISTING:
            # Asking to "keep" a key that doesn't exist: skip silently.
            # This matches the UI flow where masked fields default to
            # KEEP_EXISTING even when the underlying value is unset.
            continue
        pending_appends.append((key, incoming))

    if pending_appends:
        if new_lines and new_lines[-1] != "":
            new_lines.append("")
        for key, incoming in pending_appends:
            new_lines.append(f"{key}={_format_value(incoming)}")

    # Atomic write at 0o600.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    content = "\n".join(new_lines) + "\n" if new_lines else ""
    # Open with O_CREAT|O_WRONLY|O_TRUNC and explicit mode so even the
    # first-write case lands at 0o600. os.replace() preserves the mode.
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        # If the temp write failed, don't leave a partial .env.tmp behind.
        if tmp.exists():
            tmp.unlink()
        raise
    os.replace(tmp, path)
    # os.replace can lose permission bits on some filesystems; reassert.
    os.chmod(path, 0o600)

    return parse_env(path)


def _find_repo_root(start: Path) -> Optional[Path]:
    """Walk up from `start` looking for a `.git` dir or file. Return that
    directory, or None if we hit the filesystem root first.

    We can't `git rev-parse --show-toplevel` here because the UI must work
    when the toolkit isn't run from inside the repo (e.g. installed via
    `pip install`). Falls back gracefully in that case.
    """
    cur = start.resolve()
    for _ in range(40):  # generous depth ceiling
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def _assert_env_is_gitignored(env_path: Path) -> None:
    """Refuse to write `.env` if a `.gitignore` in the repo doesn't cover it.

    We can't read `git check-ignore` reliably (UI may not have git on PATH;
    `.env` may not exist yet so check-ignore would lie). Instead we look
    for a `.gitignore` in the discovered repo root and grep for a
    non-commented line that matches `.env` or `*.env` or `**/.env`.

    Outside a git repo (e.g. running from a pip-installed copy with no
    working tree), the check is skipped — there's no git to leak into.
    """
    repo_root = _find_repo_root(env_path.parent)
    if repo_root is None:
        return

    gitignore = repo_root / ".gitignore"
    if not gitignore.exists():
        raise ValueError(
            f"Refusing to write {env_path}: repo root {repo_root} has no .gitignore. "
            "Create one with '.env' on its own line before saving secrets."
        )

    # Compute path of env_path relative to repo root using POSIX separators
    # so gitignore patterns match consistently across platforms.
    try:
        rel = env_path.resolve().relative_to(repo_root)
    except ValueError:
        # env_path lives outside the repo (e.g. /tmp). Nothing to leak.
        return
    rel_str = rel.as_posix()
    name = env_path.name

    patterns_seen: List[str] = []
    for raw in gitignore.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        patterns_seen.append(s)
        if _gitignore_matches(s, rel_str, name):
            return

    raise ValueError(
        f"Refusing to write {env_path}: {gitignore} does not cover '{name}'. "
        f"Add a line like '.env' or '**/.env' to .gitignore first. "
        f"Patterns found: {patterns_seen}"
    )


def _gitignore_matches(pattern: str, rel_path: str, basename: str) -> bool:
    """Conservative gitignore-style pattern matcher.

    We're not reimplementing all of gitignore's semantics — just covering the
    common cases for a `.env` file:
      - bare filename: `.env`, `*.env`
      - leading-slash anchor: `/.env`
      - directory glob: `**/.env`
      - exact relative path: `dd-demo-toolkit/.env`

    Negations (`!pattern`) are ignored — they're rare for `.env` and
    erring on the side of "didn't match" just makes the gitignore check
    stricter, which is the safe direction.
    """
    import fnmatch

    if pattern.startswith("!"):
        return False
    p = pattern.lstrip("/")
    # `**/.env` → match any path ending in `.env`
    if p.startswith("**/"):
        suffix = p[3:]
        return fnmatch.fnmatch(basename, suffix) or rel_path.endswith("/" + suffix)
    # Exact-match against the relative path, or basename-only match
    return (
        fnmatch.fnmatch(rel_path, p)
        or fnmatch.fnmatch(basename, p)
        or rel_path == p
    )
