"""Local harness to exercise the NEW features without Linear/Claude.

Spins up the real FastAPI app (auth + dashboard + Slack endpoints + live
terminal) backed by a fake orchestrator that points at a real temp workspace.
Lets you:
  - load the dashboard with a bearer token,
  - open a real interactive terminal into a workspace (needs tmux),
  - hit the Slack endpoints (signature-verified) with curl.

Run:
    pip install -e ".[dev]"
    STOK_DEMO_TOKEN=devtoken python scripts/local_harness.py
    # then open http://127.0.0.1:4300/?token=devtoken

Set SLACK_SIGNING_SECRET to test the /slack/* endpoints' signature checks.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import uvicorn

from stokowski.web import create_app

TOKEN = os.environ.get("STOK_DEMO_TOKEN", "devtoken")


class _FakeNotifier:
    """Stand-in so /slack/* endpoints have a signing secret to verify against."""

    signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")
    _threads: dict[str, str] = {}

    def issue_for_thread(self, ts):
        return self._threads.get(ts)

    async def acknowledge(self, issue_id, text):
        print(f"[slack ack] {issue_id}: {text}")


class FakeOrchestrator:
    """Implements only what create_app's routes touch."""

    def __init__(self, workspace: Path):
        self._ws = workspace
        self.notifier = _FakeNotifier() if _FakeNotifier.signing_secret else None

    # dashboard / status
    def get_state_snapshot(self):
        return {
            "running": [
                {
                    "issue_identifier": "DEMO-1",
                    "project_name": "demo",
                    "status": "running",
                    "state_name": "implement",
                    "turn_count": 2,
                    "session_id": "demo-session",
                    "last_message": "Working in a real temp workspace — open the terminal ›",
                    "last_event_at": None,
                    "tokens": {"total_tokens": 1234, "input_tokens": 1000, "output_tokens": 234},
                }
            ],
            "retrying": [], "gates": [], "queued": [],
            "counts": {"running": 1, "retrying": 0, "gates": 0, "queued": 0, "projects": 1},
            "totals": {"input_tokens": 1000, "output_tokens": 234, "total_tokens": 1234, "seconds_running": 12.0},
            "projects": [{"name": "demo", "paused": False, "counts": {}, "totals": {}}],
            "pool": {},
        }

    @property
    def project_names(self):
        return ["demo"]

    def is_paused(self, name): return False
    def pause(self, name): return name in self.project_names
    def resume(self, name): return name in self.project_names
    def toggle(self, name): return False
    async def force_tick(self): pass

    # the new methods the Slack + terminal routes call
    async def apply_gate_decision(self, issue_id, decision, feedback=""):
        print(f"[gate] {issue_id} -> {decision} ({feedback})")
        return True

    async def handle_thread_feedback(self, issue_id, text):
        print(f"[thread] {issue_id}: {text}")
        return True

    def resolve_workspace(self, issue_identifier):
        # Every demo issue maps to the same real temp workspace.
        return self._ws


def main():
    ws = Path(tempfile.mkdtemp(prefix="stok-demo-"))
    # Make it a real git repo so `git diff` etc. work inside the terminal.
    subprocess.run(["git", "init", "-q"], cwd=ws)
    (ws / "README.md").write_text("# demo workspace\nEdit me, then run `git diff`.\n")
    (ws / ".stokowski").mkdir(exist_ok=True)
    (ws / ".stokowski" / "session").write_text("demo-session\n")

    app = create_app(FakeOrchestrator(ws), auth_token=TOKEN)
    print("\n" + "=" * 64)
    print(f"  workspace : {ws}")
    print(f"  dashboard : http://127.0.0.1:4300/?token={TOKEN}")
    print(f"  terminal  : http://127.0.0.1:4300/terminal/DEMO-1?token={TOKEN}  (needs tmux)")
    print(f"  slack sig : {'configured' if _FakeNotifier.signing_secret else 'NOT set (SLACK_SIGNING_SECRET)'}")
    print("=" * 64 + "\n")
    uvicorn.run(app, host="127.0.0.1", port=4300, log_level="warning")


if __name__ == "__main__":
    main()
