"""Run Claude as the agent through stokowski's real runner — no Linear needed.

This drives stokowski.runner.run_agent_turn (the exact code path the
orchestrator uses to dispatch Claude Code) against a throwaway git workspace,
streaming live events and persisting the session id the way the orchestrator
does. It proves "Claude as the agent" works inside this system and leaves a
workspace you can then attach a remote terminal to.

Prereqs: Claude Code CLI installed and authenticated (`claude` on PATH).

Run:
    python scripts/claude_agent_demo.py
    # optional: a custom task
    python scripts/claude_agent_demo.py "Write a fizzbuzz in bash and run it"
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from stokowski.config import ClaudeConfig, HooksConfig
from stokowski.models import Issue, RunAttempt
from stokowski.runner import run_agent_turn
from stokowski.workspace import read_session_id, write_session_id

DEFAULT_TASK = (
    "Create a file hello.py that prints 'hello from claude', then run it with "
    "`python3 hello.py` and confirm the output. Keep it to one short turn."
)


def on_event(identifier: str, event_type: str, event: dict) -> None:
    """Mirror the orchestrator's event handling — surface notable activity."""
    if event_type == "tool_use":
        print(f"  · tool: {event.get('name', event.get('tool', ''))}")
    elif event_type == "assistant":
        msg = event.get("message", {})
        content = msg.get("content", "")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    break
        if text:
            print(f"  · claude: {text[:160]}")
    elif event_type == "result":
        r = event.get("result", "")
        if isinstance(r, str) and r:
            print(f"  · result: {r[:160]}")


async def main(task: str) -> int:
    ws = Path(tempfile.mkdtemp(prefix="stok-claude-"))
    subprocess.run(["git", "init", "-q"], cwd=ws)

    issue = Issue(id="demo-uuid", identifier="DEMO-1", title="Claude agent smoke test")
    attempt = RunAttempt(issue_id=issue.id, issue_identifier=issue.identifier, attempt=0)

    # Scope Claude to a tool allowlist (auto-approved, no interactive prompts).
    claude_cfg = ClaudeConfig(permission_mode="allowedTools", max_turns=8)
    hooks_cfg = HooksConfig()

    print("=" * 64)
    print(f"  workspace : {ws}")
    print(f"  task      : {task}")
    print("=" * 64)
    print("Launching Claude Code as the agent...\n")

    result = await run_agent_turn(
        claude_cfg, hooks_cfg, task, ws, issue, attempt,
        on_event=on_event, env=dict(os.environ),
    )

    print("\n" + "=" * 64)
    print(f"  status     : {result.status}")
    print(f"  error      : {result.error}")
    print(f"  session_id : {result.session_id}")
    print(f"  tokens     : in={result.input_tokens} out={result.output_tokens} total={result.total_tokens}")

    # Persist the session id the way the orchestrator does, so it can be
    # resumed from a remote terminal (`claude --resume "$(cat .stokowski/session)"`).
    if result.session_id:
        write_session_id(ws, result.session_id)
        print(f"  persisted  : {ws}/.stokowski/session = {read_session_id(ws)}")

    print("\n  workspace contents:")
    for p in sorted(ws.rglob("*")):
        if ".git" not in p.parts and p.is_file():
            print(f"    {p.relative_to(ws)}")
    print("=" * 64)
    print(f"\nAttach a terminal to this workspace by pointing a harness at it,")
    print(f"or inspect it directly:  cd {ws} && git status")
    return 0 if result.status == "succeeded" else 1


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TASK
    raise SystemExit(asyncio.run(main(task)))
