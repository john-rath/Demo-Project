"""
Tests for dd_demo_toolkit_ui.env_manager.

Focus on the contracts that downstream code (the FastAPI handlers and the
UI's save flow) depends on:
  - secrets are masked on read
  - KEEP_EXISTING preserves the on-disk value
  - unknown / hand-edited keys survive a round-trip
  - file mode is 0o600 after write
  - the gitignore guard rejects writes when .env isn't ignored
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from dd_demo_toolkit_ui import env_manager as em


def _write_gitignore(repo_root: Path, *patterns: str) -> None:
    (repo_root / ".gitignore").write_text("\n".join(patterns) + "\n")


def _make_repo(tmp_path: Path) -> Path:
    """Set up a tmp dir that looks like a git repo, so the gitignore guard
    can find a .gitignore. We don't init real git here — env_manager only
    looks for a `.git` directory or file marker."""
    (tmp_path / ".git").mkdir()
    _write_gitignore(tmp_path, ".env", ".env.*", "!.env.template")
    return tmp_path


# ----- mask_secret ----------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        ("", ""),
        ("abcd", "****"),
        ("abcde", "*bcde"),
        ("abcdefgh", "****efgh"),
        ("X" * 32, "*" * 28 + "XXXX"),
    ],
)
def test_mask_secret_shows_last_four(value, expected):
    assert em.mask_secret(value) == expected


# ----- parse_env / read_env -------------------------------------------------


def test_read_env_missing_file_returns_empty(tmp_path):
    assert em.read_env(tmp_path / "nope.env") == {}


def test_read_env_masks_secrets(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "DD_API_KEY=verysecret123abcd\n"
        "DD_APP_KEY=appsecret789wxyz\n"
        "DD_SITE=datadoghq.com\n"
    )
    out = em.read_env(p, mask=True)
    assert out["DD_SITE"] == "datadoghq.com"
    assert out["DD_API_KEY"].endswith("abcd")
    assert "verysecret" not in out["DD_API_KEY"]
    assert out["DD_APP_KEY"].endswith("wxyz")


def test_read_env_unmasked_returns_real_values(tmp_path):
    p = tmp_path / ".env"
    p.write_text("DD_API_KEY=verysecret123abcd\n")
    out = em.read_env(p, mask=False)
    assert out["DD_API_KEY"] == "verysecret123abcd"


def test_read_env_handles_quoted_values(tmp_path):
    p = tmp_path / ".env"
    p.write_text('DISPLAY_NAME="My Hospital Demo"\n')
    out = em.read_env(p, mask=False)
    assert out["DISPLAY_NAME"] == "My Hospital Demo"


# ----- write_env ------------------------------------------------------------


def test_write_env_creates_file_at_mode_0600(tmp_path):
    repo = _make_repo(tmp_path)
    p = repo / ".env"
    em.write_env(p, {"DD_SITE": "datadoghq.eu"})
    assert p.exists()
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got 0o{mode:o}"


def test_write_env_preserves_comments_and_unknown_keys(tmp_path):
    repo = _make_repo(tmp_path)
    p = repo / ".env"
    p.write_text(
        "# header comment\n"
        "DD_API_KEY=existing-key-value\n"
        "MY_CUSTOM_VAR=hand-edited\n"
        "\n"
        "# section marker\n"
        "DD_SITE=datadoghq.com\n"
    )
    # Change only DD_SITE.
    em.write_env(p, {"DD_SITE": "datadoghq.eu"})
    contents = p.read_text()
    # The custom var survived.
    assert "MY_CUSTOM_VAR=hand-edited" in contents
    # Comments survived.
    assert "# header comment" in contents
    assert "# section marker" in contents
    # API key was not touched (we didn't ask).
    assert "DD_API_KEY=existing-key-value" in contents
    # DD_SITE was updated.
    assert "DD_SITE=datadoghq.eu" in contents
    assert "DD_SITE=datadoghq.com" not in contents


def test_write_env_keep_existing_preserves_secret(tmp_path):
    repo = _make_repo(tmp_path)
    p = repo / ".env"
    p.write_text("DD_API_KEY=original-value-xyz\nDD_SITE=datadoghq.com\n")
    em.write_env(p, {
        "DD_API_KEY": em.KEEP_EXISTING,
        "DD_SITE": "datadoghq.eu",
    })
    out = em.read_env(p, mask=False)
    assert out["DD_API_KEY"] == "original-value-xyz"
    assert out["DD_SITE"] == "datadoghq.eu"


def test_write_env_keep_existing_for_missing_key_is_noop(tmp_path):
    """If the user submits KEEP_EXISTING for a secret that doesn't yet exist
    on disk, we silently skip — we don't write the sentinel string."""
    repo = _make_repo(tmp_path)
    p = repo / ".env"
    em.write_env(p, {
        "DD_API_KEY": em.KEEP_EXISTING,
        "DD_SITE": "datadoghq.com",
    })
    out = em.read_env(p, mask=False)
    assert "DD_API_KEY" not in out
    assert out["DD_SITE"] == "datadoghq.com"


def test_write_env_fresh_file_has_no_blank_lines_between_keys(tmp_path):
    """Regression: an earlier bug separated every appended key with a blank
    line, producing a sparse `.env` that looks bizarre to humans. The keys
    should sit in one tight block."""
    repo = _make_repo(tmp_path)
    p = repo / ".env"
    em.write_env(p, {
        "DD_API_KEY": "op://Employee/Datadog/api-key",
        "DD_APP_KEY": "op://Employee/Datadog/app-key",
        "DD_SITE": "datadoghq.com",
        "EMIT_INTERVAL": "15",
    })
    contents = p.read_text()
    # No blank line should appear between two key=value lines.
    lines = contents.splitlines()
    for i in range(1, len(lines)):
        if lines[i] == "" and lines[i - 1] != "":
            # A blank after a key line is fine only if the file ends here.
            assert i == len(lines) - 1, (
                f"unexpected blank line between keys:\n{contents}"
            )


def test_write_env_appends_separator_before_new_block_on_existing_file(tmp_path):
    """Existing content gets exactly one blank line of separation before
    the new appended block, not zero and not many."""
    repo = _make_repo(tmp_path)
    p = repo / ".env"
    p.write_text("# header\nMY_CUSTOM=keep\n")
    em.write_env(p, {"DD_SITE": "datadoghq.com", "EMIT_INTERVAL": "15"})
    contents = p.read_text()
    # The hand-edited line and comment are preserved, then exactly one
    # blank, then the new keys tightly packed.
    assert "# header\nMY_CUSTOM=keep\n\nDD_SITE=datadoghq.com\nEMIT_INTERVAL=15\n" == contents


def test_write_env_quotes_values_with_spaces(tmp_path):
    repo = _make_repo(tmp_path)
    p = repo / ".env"
    em.write_env(p, {"DISPLAY_NAME": "My Hospital Demo"})
    contents = p.read_text()
    assert 'DISPLAY_NAME="My Hospital Demo"' in contents
    # And it round-trips.
    assert em.read_env(p, mask=False)["DISPLAY_NAME"] == "My Hospital Demo"


def test_write_env_rejects_unmanaged_keys(tmp_path):
    repo = _make_repo(tmp_path)
    p = repo / ".env"
    with pytest.raises(ValueError, match="unmanaged keys"):
        em.write_env(p, {"RANDOM_THING": "x"})


# ----- secret-reference policy ----------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        ("op://Employee/Datadog/api-key", True),
        ("op://Engineering/dd-team/api-key", True),
        ("vault:secret/dd/api_key", True),
        ("keychain://dd_api_key", True),
        # Plain values — anything that doesn't start with a known scheme.
        ("d65822a9c0570cb0aed44796c47cccdb", False),
        ("your_api_key_here", False),
        ("", False),
        # Schemes with internal whitespace — must NOT be treated as a
        # reference, otherwise a typo would sneak past the write check.
        ("op:// Employee/Datadog/api-key", False),
        ("op://Employee /Datadog/api-key", False),
        # Wrong scheme prefix.
        ("https://1password.com/...", False),
    ],
)
def test_is_secret_reference(value, expected):
    assert em.is_secret_reference(value) == expected


def test_write_env_rejects_plain_secret(tmp_path):
    repo = _make_repo(tmp_path)
    p = repo / ".env"
    with pytest.raises(em.PlainSecretRejected, match="op://Employee/Datadog/api-key"):
        em.write_env(p, {"DD_API_KEY": "d65822a9c0570cb0aed44796c47cccdb"})


def test_write_env_accepts_op_reference(tmp_path):
    repo = _make_repo(tmp_path)
    p = repo / ".env"
    em.write_env(p, {"DD_API_KEY": "op://Employee/Datadog/api-key"})
    assert em.read_env(p, mask=False)["DD_API_KEY"] == "op://Employee/Datadog/api-key"


def test_write_env_accepts_keep_existing_for_secret(tmp_path):
    """KEEP_EXISTING bypasses the plain-value check (it doesn't introduce
    a new value at all)."""
    repo = _make_repo(tmp_path)
    p = repo / ".env"
    p.write_text("DD_API_KEY=op://Employee/Datadog/api-key\n")
    em.write_env(p, {"DD_API_KEY": em.KEEP_EXISTING, "DD_SITE": "datadoghq.eu"})
    assert em.read_env(p, mask=False)["DD_API_KEY"] == "op://Employee/Datadog/api-key"


def test_write_env_accepts_empty_for_secret(tmp_path):
    """Empty string is allowed — user clearing the field to remove the key.
    The plain-value check only fires on non-empty plain strings."""
    repo = _make_repo(tmp_path)
    p = repo / ".env"
    em.write_env(p, {"DD_API_KEY": ""})  # must not raise


def test_read_env_does_not_mask_op_references(tmp_path):
    """References are addresses, not secrets — leaving them masked would
    make them uneditable. Plain values still get masked (transitional)."""
    p = tmp_path / ".env"
    p.write_text(
        "DD_API_KEY=op://Employee/Datadog/api-key\n"
        "DD_APP_KEY=plain-old-app-key-1234\n"
    )
    out = em.read_env(p, mask=True)
    assert out["DD_API_KEY"] == "op://Employee/Datadog/api-key"     # not masked
    assert out["DD_APP_KEY"].endswith("1234")                       # masked
    assert "plain" not in out["DD_APP_KEY"]


def test_non_compliant_secret_keys(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "DD_API_KEY=op://Employee/Datadog/api-key\n"
        "DD_APP_KEY=plain-old-app-key\n"
    )
    assert em.non_compliant_secret_keys(p) == ["DD_APP_KEY"]


def test_non_compliant_returns_empty_when_all_references_or_unset(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "DD_API_KEY=op://Employee/Datadog/api-key\n"
        "DD_APP_KEY=op://Employee/Datadog/app-key\n"
    )
    assert em.non_compliant_secret_keys(p) == []

    # Missing file → empty list (nothing to migrate).
    assert em.non_compliant_secret_keys(tmp_path / "nope.env") == []


# ----- gitignore guard ------------------------------------------------------


def test_write_env_refuses_when_env_not_gitignored(tmp_path):
    """No .gitignore at all → refuse, to prevent secrets landing in git."""
    (tmp_path / ".git").mkdir()
    # Intentionally NO .gitignore.
    p = tmp_path / ".env"
    with pytest.raises(ValueError, match="no .gitignore"):
        em.write_env(p, {"DD_SITE": "datadoghq.com"})


def test_write_env_refuses_when_pattern_missing(tmp_path):
    """A .gitignore exists but doesn't cover .env → refuse."""
    (tmp_path / ".git").mkdir()
    _write_gitignore(tmp_path, "node_modules/", "*.pyc")
    p = tmp_path / ".env"
    with pytest.raises(ValueError, match="does not cover"):
        em.write_env(p, {"DD_SITE": "datadoghq.com"})


def test_write_env_accepts_double_star_pattern(tmp_path):
    """`**/.env` should match a nested .env."""
    (tmp_path / ".git").mkdir()
    _write_gitignore(tmp_path, "**/.env")
    sub = tmp_path / "subdir"
    sub.mkdir()
    p = sub / ".env"
    em.write_env(p, {"DD_SITE": "datadoghq.com"})  # must not raise
    assert p.exists()


def test_write_env_outside_repo_skips_gitignore_check(tmp_path):
    """No .git ancestor → no leak risk, skip the check."""
    # Note: no `.git` dir here.
    p = tmp_path / ".env"
    em.write_env(p, {"DD_SITE": "datadoghq.com"})  # must not raise
    assert p.exists()


def test_require_gitignore_false_bypasses_check(tmp_path):
    """Tests / explicit opt-out path: the guard can be turned off."""
    (tmp_path / ".git").mkdir()
    # No .gitignore — would normally fail.
    p = tmp_path / ".env"
    em.write_env(p, {"DD_SITE": "datadoghq.com"}, require_gitignore=False)
    assert p.exists()
