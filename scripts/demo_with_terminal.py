"""One command: run Claude as the agent, then attach a live terminal to it.

1. Drives stokowski.runner.run_agent_turn (real Claude Code) against a fresh
   git workspace and persists the session id.
2. Boots the real FastAPI app (auth + dashboard + remote terminal) pointed at
   that same workspace, so you can open a browser terminal, inspect what the
   agent did, and `claude --resume "$(cat .stokowski/session)"`.

Prereqs: Claude Code CLI authenticated; `tmux` installed.

Run:
    python scripts/demo_with_terminal.py
    python scripts/demo_with_terminal.py "Write a fizzbuzz in bash and run it"
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import uvicorn

from stokowski.config import ClaudeConfig, HooksConfig
from stokowski.models import Issue, RunAttempt
from stokowski.runner import run_agent_turn
from stokowski.web import create_app
from stokowski.workspace import read_session_id, write_session_id

TOKEN = os.environ.get("STOK_DEMO_TOKEN", "devtoken")
PORT = int(os.environ.get("STOK_DEMO_PORT", "4300"))
DEFAULT_TASK = (
    "Create a file hello.py that prints 'hello from claude', then run it with "
    "`python3 hello.py` and confirm the output. Keep it to one short turn."
)


def _on_event(identifier, event_type, event):
    if event_type == "tool_use":
        print(f"  · tool: {event.get('name', event.get('tool', ''))}")
    elif event_type == "assistant":
        content = event.get("message", {}).get("content", "")
        text = content if isinstance(content, str) else next(
            (b.get("text", "") for b in content
             if isinstance(b, dict) and b.get("type") == "text"), ""
        ) if isinstance(content, list) else ""
        if text:
            print(f"  · claude: {text[:160]}")


class _Orchestrator:
    """Minimal stand-in exposing only what create_app's routes touch."""

    notifier = None

    def __init__(self, workspace: Path):
        self._ws = workspace

    def get_state_snapshot(self):
        return {
            "running": [{
                "issue_identifier": "DEMO-1", "project_name": "demo",
                "status": "running", "state_name": "implement", "turn_count": 1,
                "session_id": read_session_id(self._ws),
                "last_message": "Agent finished — open the terminal › to inspect & resume",
                "last_event_at": None,
                "tokens": {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0},
            }],
            "retrying": [], "gates": [], "queued": [],
            "counts": {"running": 1, "retrying": 0, "gates": 0, "queued": 0, "projects": 1},
            "totals": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "seconds_running": 0.0},
            "projects": [{"name": "demo", "paused": False, "counts": {}, "totals": {}}],
            "pool": {},
        }

    @property
    def project_names(self): return ["demo"]
    def is_paused(self, n): return False
    def pause(self, n): return True
    def resume(self, n): return True
    def toggle(self, n): return False
    async def force_tick(self): pass
    async def apply_gate_decision(self, *a, **k): return True
    async def handle_thread_feedback(self, *a, **k): return True
    def resolve_workspace(self, issue_identifier): return self._ws


async def run_agent(task: str) -> Path:
    ws = Path(tempfile.mkdtemp(prefix="stok-demo-"))
    subprocess.run(["git", "init", "-q"], cwd=ws)
    issue = Issue(id="demo-uuid", identifier="DEMO-1", title="Claude agent demo")
    attempt = RunAttempt(issue_id=issue.id, issue_identifier=issue.identifier, attempt=0)
    cfg = ClaudeConfig(permission_mode="allowedTools", max_turns=8)

    print("=" * 64)
    print(f"  task : {task}\n  ws   : {ws}")
    print("=" * 64 + "\nLaunching Claude Code as the agent...\n")
    result = await run_agent_turn(cfg, HooksConfig(), task, ws, issue, attempt,
                                  on_event=_on_event, env=dict(os.environ))
    print(f"\n  status={result.status} tokens={result.total_tokens} session={result.session_id}")
    if result.session_id:
        write_session_id(ws, result.session_id)
    return ws


def main():
    task = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TASK
    ws = asyncio.run(run_agent(task))

    app = create_app(_Orchestrator(ws), auth_token=TOKEN)
    print("\n" + "=" * 64)
    print("  Agent done. Live terminal is starting — Ctrl-C to stop.")
    print(f"  dashboard : http://127.0.0.1:{PORT}/?token={TOKEN}")
    print(f"  terminal  : http://127.0.0.1:{PORT}/terminal/DEMO-1?token={TOKEN}")
    print(f"  inside it : ls; cat hello.py; claude --resume \"$(cat .stokowski/session)\"")
    print("=" * 64 + "\n")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
