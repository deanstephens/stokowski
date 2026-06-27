"""tmux-backed interactive sessions for per-issue workspaces.

Each issue gets a long-lived tmux session rooted in its workspace, so a human
can attach at any time (via the web terminal in web.py) to inspect or drive the
agent — run `git diff`, resume the headless session with
`claude --resume "$(cat .stokowski/session)"`, etc.

tmux is an external dependency. If it is not installed these helpers raise
TmuxUnavailable with an actionable message rather than failing obscurely.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from .workspace import sanitize_key

logger = logging.getLogger("stokowski.terminal")

SESSION_PREFIX = "stok-"


class TmuxUnavailable(RuntimeError):
    """Raised when the tmux binary is not available on PATH."""


def tmux_available() -> bool:
    return shutil.which("tmux") is not None


def session_name(issue_identifier: str) -> str:
    """tmux session name for an issue. tmux forbids '.' and ':' in names."""
    key = sanitize_key(issue_identifier).replace(".", "_")
    return f"{SESSION_PREFIX}{key}"


def _require_tmux() -> None:
    if not tmux_available():
        raise TmuxUnavailable(
            "tmux is not installed. Install it to use interactive terminals "
            "(macOS: `brew install tmux`, Debian/Ubuntu: `apt install tmux`)."
        )


async def _tmux(*args: str) -> tuple[int, str]:
    """Run a tmux command, returning (returncode, combined output)."""
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace")


async def has_session(issue_identifier: str) -> bool:
    if not tmux_available():
        return False
    rc, _ = await _tmux("has-session", "-t", session_name(issue_identifier))
    return rc == 0


async def _session_workspace(name: str) -> str | None:
    """Return the workspace a session was created for (tagged at creation)."""
    rc, out = await _tmux("show-environment", "-t", name, "STOK_WS")
    if rc != 0:
        return None
    # Output is "STOK_WS=/path" (or "-STOK_WS" when unset).
    line = out.strip()
    if line.startswith("STOK_WS="):
        return line[len("STOK_WS="):]
    return None


async def ensure_session(issue_identifier: str, workspace: Path) -> str:
    """Create the issue's tmux session (rooted at `workspace`) if absent.

    Returns the session name. If a session with this name already exists but
    was created for a *different* workspace (e.g. a stale session left over
    after a workspace was removed and recreated), it is killed and replaced so
    the terminal never attaches to the wrong directory. Raises TmuxUnavailable
    if tmux is missing or RuntimeError if the workspace path does not exist.
    """
    _require_tmux()
    if not workspace.exists():
        raise RuntimeError(f"Workspace does not exist: {workspace}")
    name = session_name(issue_identifier)
    target = str(workspace.resolve())
    if await has_session(issue_identifier):
        recorded = await _session_workspace(name)
        if recorded == target:
            return name
        # Stale/mismatched session — replace it.
        logger.info(f"Replacing stale tmux session {name} (was {recorded!r})")
        await _tmux("kill-session", "-t", name)
    rc, out = await _tmux(
        "new-session", "-d", "-s", name, "-c", target
    )
    if rc != 0:
        raise RuntimeError(f"failed to create tmux session {name}: {out.strip()}")
    # Tag the session with its workspace so future reuse can be validated.
    await _tmux("set-environment", "-t", name, "STOK_WS", target)
    logger.info(f"Created tmux session {name} at {target}")
    return name


async def kill_session(issue_identifier: str) -> bool:
    if not tmux_available():
        return False
    rc, _ = await _tmux("kill-session", "-t", session_name(issue_identifier))
    return rc == 0


async def list_sessions() -> list[str]:
    """List stokowski-managed tmux session names."""
    if not tmux_available():
        return []
    rc, out = await _tmux("list-sessions", "-F", "#{session_name}")
    if rc != 0:
        return []
    return [
        line.strip()
        for line in out.splitlines()
        if line.strip().startswith(SESSION_PREFIX)
    ]
