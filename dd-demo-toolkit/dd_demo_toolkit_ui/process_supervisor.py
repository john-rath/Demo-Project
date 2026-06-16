"""
Subprocess lifecycle + log-streaming for the web UI.

The UI doesn't run the simulator / setup / teardown in-process. Each is a
separate `docker compose ...` invocation spawned as a child of the FastAPI
server, with stdout+stderr captured into a bounded ring buffer and
multicast to any number of SSE subscribers.

Design rules:

1. **One supervisor, one app.** Single instance lives on the FastAPI app,
   shared across all request handlers. Process state survives a browser
   reload (state is server-side, not in the browser session) so closing
   the tab doesn't kill the simulator.

2. **No `op run` here.** The UI server is expected to have been launched
   via `make ui`, which wraps the whole server in `op run --env-file=.env --`.
   Spawned children inherit that resolved env. If the server was launched
   without op (env still has `op://...` literals), ``start()`` refuses
   with a clear error rather than letting the child fail mysteriously
   30 seconds later with a 403 from Datadog.

3. **Bounded buffers.** Each process has a ``deque(maxlen=N)``. A simulator
   that runs for a week won't OOM the UI. SSE subscribers replay the
   buffered backlog on connect, then receive live lines.

4. **Graceful stop.** SIGINT first (= Ctrl-C, which `docker compose up`
   handles by stopping containers in order). After ``grace_seconds``
   without exit, SIGKILL on the process group.

5. **State machine.** Each named process is in one of:
     IDLE        — never started, or last run finished
     RUNNING     — proc alive (spawned by us) OR adopted from Docker
     STOPPING    — stop signal sent / compose down running
     EXITED      — last run finished; exit_code captured
   The UI uses this to enable/disable Start vs Stop buttons.

6. **Reconciliation.** Long-running services (simulator) may be started
   outside the UI (``make up``, ``make ui``). ``reconcile()`` queries
   ``docker compose ps`` and, if the service is already running, adopts
   it: transitions to RUNNING, streams its logs via
   ``docker compose logs --follow``, and wires Stop to run
   ``docker compose down``. Called on every status endpoint so the UI
   always reflects real Docker state regardless of how containers started.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional

logger = logging.getLogger(__name__)


# ----- Command map ----------------------------------------------------------


# Logical process name → argv. Cwd is the project_dir passed into the
# supervisor (where docker-compose.yaml + .env live).
#
# We invoke `docker compose` directly rather than `make up` / `make setup`
# so that signal handling is predictable. `make` proxies signals to its
# child, but introducing an extra process layer just for symmetry with
# the CLI flow buys nothing.
#
# Adding a new process: add an entry here AND to the four-tuple
# (start command, stop command, kind) in PROCESS_DEFS.
PROCESS_DEFS: Dict[str, Dict[str, object]] = {
    "simulator": {
        # `--build` rebuilds the simulator image before starting. Builds
        # are incremental — unchanged COPY layers are cached, so a no-op
        # build is ~2-5s. When the toolkit Python (engine, plugins,
        # overlays) HAS changed, the build picks it up and compose
        # detects the new image SHA + recreates the container. Without
        # `--build`, code edits required a separate `make build` or
        # `docker compose build simulator` step before Start, which was
        # a recurring footgun during EY-overlay iteration.
        "argv": ["docker", "compose", "up", "--build", "--remove-orphans"],
        # For long-running services, "stop" sends SIGINT to the `up` proc
        # (compose's documented graceful-stop signal). Containers shut
        # down in dependency order. A separate `docker compose down` cleans
        # up volumes / orphans afterward.
        "stop_signal": signal.SIGINT,
        "stop_followup_argv": ["docker", "compose", "down", "--remove-orphans"],
        "long_running": True,
    },
    "setup": {
        "argv": [
            "docker", "compose", "--profile", "setup",
            "run", "--rm", "--remove-orphans", "setup",
        ],
        # One-shots are SIGTERM (slightly faster exit than SIGINT for the
        # `docker compose run` wrapper).
        "stop_signal": signal.SIGTERM,
        "stop_followup_argv": None,
        "long_running": False,
    },
    "teardown": {
        "argv": [
            "docker", "compose", "--profile", "teardown",
            "run", "--rm", "--remove-orphans", "teardown",
        ],
        "stop_signal": signal.SIGTERM,
        "stop_followup_argv": None,
        "long_running": False,
    },
    "teardown-all": {
        "argv": [
            "docker", "compose", "--profile", "teardown-all",
            "run", "--rm", "--remove-orphans", "teardown-all",
        ],
        "stop_signal": signal.SIGTERM,
        "stop_followup_argv": None,
        "long_running": False,
    },
}


# ----- Errors ---------------------------------------------------------------


class ProcessSupervisorError(Exception):
    """Base for supervisor errors. Handlers translate to HTTP 4xx with the
    message preserved for the UI."""


class UnknownProcessError(ProcessSupervisorError):
    """No PROCESS_DEFS entry for the given name."""


class AlreadyRunningError(ProcessSupervisorError):
    """start() called for a process that's already RUNNING."""


class NotRunningError(ProcessSupervisorError):
    """stop() called for a process that isn't RUNNING."""


class EnvNotResolvedError(ProcessSupervisorError):
    """Refused to spawn because DD_API_KEY / DD_APP_KEY in os.environ are
    still ``op://...`` references — the parent UI server wasn't launched
    via `op run`. Spawning the child would result in 403 errors from
    Datadog after a confusing delay. Fail fast instead.
    """


# ----- State ---------------------------------------------------------------


class ProcessState(str, Enum):
    IDLE = "idle"          # never started, or fully reset
    RUNNING = "running"    # child proc alive
    STOPPING = "stopping"  # stop signal sent, waiting for exit
    EXITED = "exited"      # last run finished; exit_code captured


@dataclass
class ProcessHandle:
    """Per-process state. Created lazily by ``_get_or_create`` on first use."""
    name: str
    state: ProcessState = ProcessState.IDLE
    proc: Optional[asyncio.subprocess.Process] = None
    pid: Optional[int] = None
    started_at: Optional[float] = None   # epoch seconds
    exit_code: Optional[int] = None
    last_error: Optional[str] = None
    # True when the process was detected via docker compose ps rather than
    # spawned by us. Stop uses `compose down` instead of killpg in this case.
    adopted: bool = False
    # Line buffer + subscribers. We keep the deque on the handle and push
    # new lines to every subscriber's queue simultaneously.
    log_buffer: deque = field(default_factory=lambda: deque(maxlen=5000))
    log_subscribers: List["asyncio.Queue[Optional[str]]"] = field(default_factory=list)
    # Reader task that drains the child's stdout into the buffer.
    reader_task: Optional[asyncio.Task] = None
    # Wait task that observes child exit and updates state.
    waiter_task: Optional[asyncio.Task] = None


# ----- Supervisor ----------------------------------------------------------


class ProcessSupervisor:
    """Owns all ProcessHandles for one UI server instance."""

    def __init__(
        self,
        *,
        project_dir: Path,
        max_log_lines: int = 5000,
        stop_grace_seconds: float = 10.0,
    ):
        self.project_dir = project_dir
        self.max_log_lines = max_log_lines
        self.stop_grace_seconds = stop_grace_seconds
        self.handles: Dict[str, ProcessHandle] = {}
        # asyncio.Lock per process name, created on demand. Prevents start/
        # stop racing each other for the same process.
        self._locks: Dict[str, asyncio.Lock] = {}

    # ---- public API ----

    def names(self) -> List[str]:
        """Logical process names this supervisor knows about."""
        return sorted(PROCESS_DEFS.keys())

    def status(self, name: str) -> Dict[str, object]:
        """Return a serialisable status dict for a single process."""
        self._validate_name(name)
        h = self.handles.get(name)
        if h is None:
            return {
                "name": name,
                "state": ProcessState.IDLE.value,
                "pid": None,
                "started_at": None,
                "uptime_seconds": None,
                "exit_code": None,
                "last_error": None,
                "log_lines_buffered": 0,
            }
        uptime = (
            time.time() - h.started_at
            if h.started_at is not None and h.state in (ProcessState.RUNNING, ProcessState.STOPPING)
            else None
        )
        return {
            "name": h.name,
            "state": h.state.value,
            "pid": h.pid,
            "started_at": h.started_at,
            "uptime_seconds": uptime,
            "exit_code": h.exit_code,
            "last_error": h.last_error,
            "log_lines_buffered": len(h.log_buffer),
        }

    def status_all(self) -> List[Dict[str, object]]:
        """Status of every known process. Used by the UI on initial load."""
        return [self.status(n) for n in self.names()]

    async def start(self, name: str) -> Dict[str, object]:
        """Spawn the child for ``name``. Returns the new status dict.

        Idempotent within a state: starting an already-RUNNING process
        raises ``AlreadyRunningError`` (the UI's Start button should be
        disabled in that case; the error is the backstop).

        Restarting after EXITED is allowed and resets the handle.
        """
        self._validate_name(name)
        self._validate_env_resolved()
        async with self._lock(name):
            h = self._get_or_create(name)
            if h.state == ProcessState.RUNNING:
                raise AlreadyRunningError(
                    f"'{name}' is already running (pid {h.pid})."
                )
            if h.state == ProcessState.STOPPING:
                raise AlreadyRunningError(
                    f"'{name}' is shutting down. Wait for it to finish."
                )
            # Fresh start: reset transient state but keep log_buffer so
            # the user sees the previous run's tail in the UI on reconnect.
            # Actually we DO want a clean buffer per run — otherwise the
            # UI shows stale output mixed with new. Clear it.
            h.log_buffer.clear()
            h.exit_code = None
            h.last_error = None

            argv = self._argv_for(name)
            logger.info("supervisor: starting %s: %s", name, " ".join(argv))
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,  # merge so order is preserved
                    cwd=str(self.project_dir),
                    # New process group so SIGINT/SIGTERM can hit the
                    # whole tree (docker compose forks ssh-agent helpers
                    # etc. for some operations).
                    start_new_session=True,
                )
            except FileNotFoundError as e:
                # docker not installed, or compose plugin missing.
                h.state = ProcessState.EXITED
                h.last_error = (
                    f"Could not exec {argv[0]!r}: {e}. "
                    "Is Docker installed and on PATH?"
                )
                raise ProcessSupervisorError(h.last_error)

            h.proc = proc
            h.pid = proc.pid
            h.started_at = time.time()
            h.state = ProcessState.RUNNING

            # Spawn the reader + waiter as long-lived tasks. They live
            # past this handler's return — that's intentional; the
            # subscriber endpoints reach into the same buffer.
            h.reader_task = asyncio.create_task(
                self._drain_stdout(h),
                name=f"supervisor.reader[{name}]",
            )
            h.waiter_task = asyncio.create_task(
                self._wait_for_exit(h),
                name=f"supervisor.waiter[{name}]",
            )

        return self.status(name)

    async def stop(self, name: str) -> Dict[str, object]:
        """Signal the child to exit. Returns the (now STOPPING) status.

        Caller can poll ``status()`` to see when state transitions to EXITED.
        ``stop()`` returns as soon as the signal is sent — it does NOT wait
        for the child to finish, because the UI wants the button click to
        feel responsive. The reader/waiter tasks handle the rest.
        """
        self._validate_name(name)
        async with self._lock(name):
            h = self.handles.get(name)
            if h is None or h.state == ProcessState.IDLE:
                raise NotRunningError(f"'{name}' isn't running.")
            if h.state == ProcessState.EXITED:
                raise NotRunningError(
                    f"'{name}' already exited (code {h.exit_code})."
                )
            if h.state == ProcessState.STOPPING:
                # Second click on Stop. Escalate to SIGKILL on the group.
                logger.warning("supervisor: %s already stopping; escalating to SIGKILL", name)
                self._kill_group(h, signal.SIGKILL)
                return self.status(name)

            h.state = ProcessState.STOPPING
            if h.adopted or h.proc is None:
                # Container was started outside the UI — use compose down.
                followup = PROCESS_DEFS[name].get("stop_followup_argv")
                down_argv = self._inject_profiles(
                    followup or ["docker", "compose", "down", "--remove-orphans"]
                )
                logger.info("supervisor: stopping adopted %s via %s", name, " ".join(down_argv))
                asyncio.create_task(
                    self._compose_down(h, down_argv),
                    name=f"supervisor.compose_down[{name}]",
                )
            else:
                stop_signal = PROCESS_DEFS[name]["stop_signal"]
                assert isinstance(stop_signal, signal.Signals)
                logger.info("supervisor: stopping %s with %s", name, stop_signal.name)
                self._kill_group(h, stop_signal)
                # The grace timer escalates to SIGKILL if the child doesn't
                # exit. Fire-and-forget — _wait_for_exit awaits the proc.
                asyncio.create_task(
                    self._grace_timer(h),
                    name=f"supervisor.grace[{name}]",
                )
        return self.status(name)

    async def subscribe_logs(
        self,
        name: str,
        *,
        replay_buffer: bool = True,
    ) -> AsyncIterator[str]:
        """Async generator yielding stdout lines (without trailing newline).

        Yields the buffered backlog first (if ``replay_buffer``), then
        live lines as they arrive. Yields the sentinel ``None`` (filtered
        before being yielded to the consumer) when the process exits, to
        let the SSE handler close the stream cleanly.

        Usage:
            async for line in supervisor.subscribe_logs("simulator"):
                ...

        Multiple subscribers are independent — each gets its own queue.
        """
        self._validate_name(name)
        h = self._get_or_create(name)
        queue: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=1000)

        # Replay buffer atomically with subscription so a line emitted
        # during replay isn't lost or duplicated.
        if replay_buffer:
            for line in list(h.log_buffer):
                await queue.put(line)
        h.log_subscribers.append(queue)
        try:
            while True:
                item = await queue.get()
                if item is None:
                    # End-of-stream sentinel (proc exited).
                    return
                yield item
        finally:
            try:
                h.log_subscribers.remove(queue)
            except ValueError:
                pass  # already removed during cleanup

    # ---- internals ----

    def _validate_name(self, name: str) -> None:
        if name not in PROCESS_DEFS:
            raise UnknownProcessError(
                f"unknown process '{name}'. Known: {sorted(PROCESS_DEFS)}"
            )

    def _argv_for(self, name: str) -> List[str]:
        argv = PROCESS_DEFS[name]["argv"]
        assert isinstance(argv, list)
        argv = list(argv)
        # The long-running "simulator" `up` should bring along the same
        # opt-in profiles the Makefile's `make up` does, so Start in the UI
        # builds + launches the full stack (mock-app mesh, DBM) — not just the
        # default otel-collector + simulator. One-shots (setup/teardown) keep
        # their own --profile and are left untouched.
        if PROCESS_DEFS[name].get("long_running"):
            argv = self._inject_profiles(argv)
        return argv

    def _profile_args(self) -> List[str]:
        """Profile flags derived from .env, mirroring the Makefile rules:
        DD_DEMO_MOCK_FLEET=true → mock-app; DD_DEMO_DBM=true or
        DD_DEMO_SUB_VERTICAL=payment-processor → dbm."""
        flags: Dict[str, str] = {}
        try:
            for line in (self.project_dir / ".env").read_text(encoding="utf-8").splitlines():
                s = line.strip()
                for key in ("DD_DEMO_MOCK_FLEET", "DD_DEMO_DBM", "DD_DEMO_SUB_VERTICAL"):
                    if s.startswith(key + "="):
                        flags[key] = s.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            return []
        profiles: List[str] = []
        if flags.get("DD_DEMO_MOCK_FLEET", "").lower() == "true":
            profiles.append("mock-app")
        if flags.get("DD_DEMO_DBM", "").lower() == "true" or \
                flags.get("DD_DEMO_SUB_VERTICAL", "") == "payment-processor":
            profiles.append("dbm")
        out: List[str] = []
        for p in profiles:
            out += ["--profile", p]
        return out

    def _inject_profiles(self, argv: List[str]) -> List[str]:
        """Insert the active --profile flags right after the `compose` token."""
        profiles = self._profile_args()
        if not profiles or "compose" not in argv:
            return list(argv)
        out = list(argv)
        i = out.index("compose") + 1
        return out[:i] + profiles + out[i:]

    def _lock(self, name: str) -> asyncio.Lock:
        lock = self._locks.get(name)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[name] = lock
        return lock

    def _get_or_create(self, name: str) -> ProcessHandle:
        h = self.handles.get(name)
        if h is None:
            h = ProcessHandle(
                name=name,
                log_buffer=deque(maxlen=self.max_log_lines),
            )
            self.handles[name] = h
        return h

    def _validate_env_resolved(self) -> None:
        """Refuse to spawn if DD_API_KEY / DD_APP_KEY are still op:// refs.

        This catches the "user launched dd-demo-ui directly instead of via
        `make ui`" case before it produces 403s from Datadog 30 seconds
        later.
        """
        bad: Dict[str, str] = {}
        for key in ("DD_API_KEY", "DD_APP_KEY"):
            v = os.environ.get(key, "")
            if v.startswith(("op://", "vault:", "keychain://")):
                bad[key] = v
        if bad:
            msg = (
                "The UI server's env has unresolved secret references: "
                + ", ".join(f"{k}={v!r}" for k, v in bad.items())
                + ". Launch the UI via `make ui` (which wraps `op run "
                "--env-file=.env --`) instead of running `dd-demo-ui` "
                "directly."
            )
            raise EnvNotResolvedError(msg)

    def _kill_group(self, h: ProcessHandle, sig: signal.Signals) -> None:
        """Send ``sig`` to the child's process group.

        We started the child with start_new_session=True, so it's a session
        leader and pgid == pid. Killing the group catches any helpers
        (docker compose spawns them; otherwise SIGINT to the parent might
        leave them dangling).
        """
        if h.proc is None or h.pid is None:
            return
        try:
            os.killpg(h.pid, sig)
        except ProcessLookupError:
            # Already exited.
            pass
        except PermissionError as e:
            logger.error("supervisor: killpg(%s, %s) failed: %s", h.pid, sig, e)
            h.last_error = f"killpg failed: {e}"

    async def _grace_timer(self, h: ProcessHandle) -> None:
        """SIGKILL escalation after `stop_grace_seconds`."""
        await asyncio.sleep(self.stop_grace_seconds)
        if h.state == ProcessState.STOPPING:
            logger.warning(
                "supervisor: %s didn't exit in %.1fs; SIGKILL",
                h.name, self.stop_grace_seconds,
            )
            self._kill_group(h, signal.SIGKILL)

    async def _drain_stdout(self, h: ProcessHandle) -> None:
        """Read child's stdout line-by-line, push to buffer + subscribers.

        Runs until the pipe closes (which happens when the child exits).
        """
        assert h.proc is not None and h.proc.stdout is not None
        while True:
            line_bytes = await h.proc.stdout.readline()
            if not line_bytes:
                # EOF. Notify subscribers and stop.
                self._broadcast_end(h)
                return
            try:
                line = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
            except Exception as e:  # extremely defensive
                line = f"<decode error: {e}>"
            h.log_buffer.append(line)
            self._broadcast(h, line)

    def _broadcast(self, h: ProcessHandle, line: str) -> None:
        """Push to every live subscriber. Drops the line for subscribers
        whose queue is full (slow consumer; better to drop than block
        the whole pipeline).
        """
        for q in list(h.log_subscribers):
            try:
                q.put_nowait(line)
            except asyncio.QueueFull:
                # Drop — slow consumer. They'll see a gap in the stream.
                logger.warning(
                    "supervisor: dropped line for slow subscriber on %s",
                    h.name,
                )

    def _broadcast_end(self, h: ProcessHandle) -> None:
        """Signal end-of-stream to all subscribers."""
        for q in list(h.log_subscribers):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass

    async def _wait_for_exit(self, h: ProcessHandle) -> None:
        """Await proc exit, update state, run followup (e.g. `compose down`).

        For long-running services with a `stop_followup_argv`, we ONLY run
        the followup if we got here via a stop() call. A natural exit
        (e.g. docker compose exiting after a failure) shouldn't trigger
        a teardown.
        """
        assert h.proc is not None
        exit_code = await h.proc.wait()
        # Drain reader to completion so the buffer is fully populated.
        if h.reader_task is not None:
            try:
                await h.reader_task
            except Exception:
                pass

        was_stopping = h.state == ProcessState.STOPPING
        h.exit_code = exit_code
        h.state = ProcessState.EXITED
        if exit_code != 0 and not was_stopping:
            h.last_error = f"exited with code {exit_code}"
        logger.info(
            "supervisor: %s exited with code %d (was_stopping=%s)",
            h.name, exit_code, was_stopping,
        )

        # Optional follow-up command (currently only `compose down` after
        # simulator stop). Run as fire-and-forget — its output isn't streamed
        # because the user has already moved on; we just need cleanup.
        followup = PROCESS_DEFS[h.name].get("stop_followup_argv")
        if was_stopping and followup:
            followup = self._inject_profiles(followup)
            logger.info("supervisor: running follow-up for %s: %s", h.name, followup)
            try:
                proc = await asyncio.create_subprocess_exec(
                    *followup,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    cwd=str(self.project_dir),
                )
                await proc.wait()
            except Exception as e:
                logger.warning("supervisor: follow-up failed for %s: %s", h.name, e)

    # ---- reconciliation (detect externally-started containers) ---------------

    async def reconcile(self, name: str) -> None:
        """Adopt a long-running service that was started outside the UI.

        Queries ``docker compose ps`` for the named service. If it is running
        but the supervisor thinks it is IDLE or EXITED, transitions to RUNNING
        and starts streaming its logs via ``docker compose logs --follow``.
        This makes Stop work correctly regardless of how the containers started.
        """
        if not PROCESS_DEFS.get(name, {}).get("long_running"):
            return
        async with self._lock(name):
            h = self._get_or_create(name)
            if h.state in (ProcessState.RUNNING, ProcessState.STOPPING):
                return  # already tracking — don't interfere
            if not await self._is_service_running(name):
                return  # not running in Docker either
            # Containers are up but supervisor doesn't know — adopt them.
            logger.info("supervisor: adopting externally-started service '%s'", name)
            h.state = ProcessState.RUNNING
            h.proc = None
            h.pid = None
            h.adopted = True
            h.exit_code = None
            h.last_error = None
            if h.started_at is None:
                h.started_at = time.time()
            # Stream logs from the running container. The task also handles
            # the exit transition when the container eventually stops.
            if h.reader_task is None or h.reader_task.done():
                h.reader_task = asyncio.create_task(
                    self._drain_docker_logs(h, name),
                    name=f"supervisor.docker_logs[{name}]",
                )

    async def reconcile_long_running(self) -> None:
        """Reconcile all long-running services."""
        for name, defn in PROCESS_DEFS.items():
            if defn.get("long_running"):
                await self.reconcile(name)

    async def _is_service_running(self, service_name: str) -> bool:
        """Return True if the named compose service has at least one running container."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "compose", "ps", "--format", "json", service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(self.project_dir),
            )
            stdout, _ = await proc.communicate()
        except FileNotFoundError:
            return False
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                if json.loads(line).get("State") == "running":
                    return True
            except json.JSONDecodeError:
                pass
        return False

    async def _drain_docker_logs(self, h: ProcessHandle, service_name: str) -> None:
        """Stream ``docker compose logs --follow`` for an adopted service.

        Runs until the log stream closes (container stopped) or until the
        handle transitions away from RUNNING (e.g. Stop was clicked). Also
        handles the EXITED transition so the UI updates correctly.
        """
        argv = [
            "docker", "compose", "logs", "--follow", "--no-log-prefix",
            service_name,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(self.project_dir),
            )
            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                # Bail early if Stop has been requested; compose down will
                # drain remaining output via its own process.
                if h.state == ProcessState.STOPPING:
                    proc.terminate()
                    break
                line = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
                h.log_buffer.append(line)
                self._broadcast(h, line)
        except Exception as e:
            logger.warning("supervisor: docker logs drain error for %s: %s", service_name, e)
        finally:
            # Only transition to EXITED if we weren't already set to STOPPING
            # by a Stop click (compose_down will handle that path).
            if h.state == ProcessState.RUNNING:
                h.state = ProcessState.EXITED
                h.exit_code = 0
                h.adopted = False
                h.started_at = None
            self._broadcast_end(h)

    async def _compose_down(self, h: ProcessHandle, argv: List[str]) -> None:
        """Run ``docker compose down`` for an adopted service, then mark EXITED."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(self.project_dir),
            )
            await proc.communicate()
        except Exception as e:
            logger.warning("supervisor: compose_down failed: %s", e)
            h.last_error = f"compose down failed: {e}"
        h.exit_code = 0
        h.state = ProcessState.EXITED
        h.adopted = False
        h.started_at = None
        self._broadcast_end(h)

    async def shutdown(self) -> None:
        """Best-effort: stop every running process and wait for exit.

        Called from the FastAPI lifespan shutdown hook so closing the
        UI doesn't leave docker compose orphaned.
        """
        running = [
            h for h in self.handles.values()
            if h.state in (ProcessState.RUNNING, ProcessState.STOPPING)
        ]
        for h in running:
            try:
                await self.stop(h.name)
            except ProcessSupervisorError:
                pass
        # Wait briefly for things to actually exit.
        for h in running:
            if h.waiter_task is not None and not h.waiter_task.done():
                try:
                    await asyncio.wait_for(h.waiter_task, timeout=self.stop_grace_seconds + 2)
                except asyncio.TimeoutError:
                    self._kill_group(h, signal.SIGKILL)
