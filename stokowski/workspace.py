"""Workspace management - create, reuse, and clean per-issue workspaces."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import HooksConfig

logger = logging.getLogger("stokowski.workspace")


def sanitize_key(identifier: str) -> str:
    """Replace non-safe chars with underscore for directory name."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", identifier)


# Per-workspace metadata lives under this hidden directory.
META_DIRNAME = ".stokowski"
SESSION_FILENAME = "session"


def write_session_id(ws_path: Path, session_id: str) -> None:
    """Persist an agent session id inside the workspace.

    Lets the agent session survive an orchestrator restart so a continuation
    turn can `claude --resume` it. Failures are non-fatal.
    """
    if not session_id:
        return
    try:
        meta = ws_path / META_DIRNAME
        meta.mkdir(parents=True, exist_ok=True)
        (meta / SESSION_FILENAME).write_text(session_id.strip() + "\n")
    except Exception as e:  # disk issues must never break the run loop
        logger.debug(f"could not persist session id at {ws_path}: {e}")


def read_session_id(ws_path: Path) -> str | None:
    """Read a previously persisted agent session id, or None."""
    try:
        p = ws_path / META_DIRNAME / SESSION_FILENAME
        if p.exists():
            val = p.read_text().strip()
            return val or None
    except Exception:
        pass
    return None


CONVERSATION_FILENAME = "conversation.jsonl"


def append_conversation(ws_path: Path, role: str, text: str) -> None:
    """Append one turn to the issue's gate conversation transcript.

    `role` is "human" or "agent". Stored as JSON lines under the workspace's
    `.stokowski/` dir so the discussion survives restarts and can be injected
    into later stage prompts. Failures are non-fatal.
    """
    text = (text or "").strip()
    if not text:
        return
    try:
        import json

        meta = ws_path / META_DIRNAME
        meta.mkdir(parents=True, exist_ok=True)
        with (meta / CONVERSATION_FILENAME).open("a") as f:
            f.write(json.dumps({"role": role, "text": text}) + "\n")
    except Exception as e:
        logger.debug(f"could not append conversation at {ws_path}: {e}")


def read_conversation(ws_path: Path) -> list[dict]:
    """Read the gate conversation transcript as a list of {role, text}."""
    import json

    out: list[dict] = []
    try:
        p = ws_path / META_DIRNAME / CONVERSATION_FILENAME
        if not p.exists():
            return out
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if isinstance(d, dict) and d.get("text"):
                    out.append({"role": d.get("role", "human"), "text": d["text"]})
            except ValueError:
                continue
    except Exception:
        pass
    return out


@dataclass
class WorkspaceResult:
    path: Path
    workspace_key: str
    created_now: bool


async def run_hook(script: str, cwd: Path, timeout_ms: int, label: str) -> bool:
    """Run a shell hook script in the workspace directory. Returns True on success."""
    logger.info(f"hook={label} cwd={cwd}")
    try:
        proc = await asyncio.create_subprocess_shell(
            script,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_ms / 1000
        )
        if proc.returncode != 0:
            logger.error(
                f"hook={label} failed rc={proc.returncode} stderr={stderr.decode()[:500]}"
            )
            return False
        return True
    except asyncio.TimeoutError:
        logger.error(f"hook={label} timed out after {timeout_ms}ms")
        proc.kill()
        return False
    except Exception as e:
        logger.error(f"hook={label} error: {e}")
        return False


async def ensure_workspace(
    workspace_root: Path,
    issue_identifier: str,
    hooks: HooksConfig,
) -> WorkspaceResult:
    """Create or reuse a workspace for an issue."""
    key = sanitize_key(issue_identifier)
    ws_path = workspace_root / key

    # Safety: workspace must be under root
    ws_abs = ws_path.resolve()
    root_abs = workspace_root.resolve()
    if not ws_abs.is_relative_to(root_abs):
        raise ValueError(f"Workspace path {ws_abs} escapes root {root_abs}")

    created_now = not ws_path.exists()
    ws_path.mkdir(parents=True, exist_ok=True)

    if created_now and hooks.after_create:
        ok = await run_hook(hooks.after_create, ws_path, hooks.timeout_ms, "after_create")
        if not ok:
            # Clean up failed workspace
            shutil.rmtree(ws_path, ignore_errors=True)
            raise RuntimeError(f"after_create hook failed for {issue_identifier}")

    return WorkspaceResult(path=ws_path, workspace_key=key, created_now=created_now)


async def remove_workspace(
    workspace_root: Path,
    issue_identifier: str,
    hooks: HooksConfig,
) -> None:
    """Remove a workspace directory for a terminal issue."""
    key = sanitize_key(issue_identifier)
    ws_path = workspace_root / key

    if not ws_path.exists():
        return

    if hooks.before_remove:
        await run_hook(hooks.before_remove, ws_path, hooks.timeout_ms, "before_remove")

    logger.info(f"Removing workspace issue={issue_identifier} path={ws_path}", extra={"linked_to": issue_identifier})
    shutil.rmtree(ws_path, ignore_errors=True)
