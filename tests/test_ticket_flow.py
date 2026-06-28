"""End-to-end-ish test of the Slack ticket-draft flow wiring (#19)."""

import asyncio
import types
from pathlib import Path

from stokowski.models import Issue
from stokowski.orchestrator import MultiOrchestrator

WORKFLOW = """
tracker:
  kind: linear
  api_key: k
  project_slug: p
slack:
  enabled: true
  bot_token: t
  channel: C1
  ticket_creation: true
linear_states:
  todo: "Todo"
states:
  build:
    type: agent
    prompt: build.md
    linear_state: active
    transitions:
      complete: done
  done:
    type: terminal
    linear_state: terminal
"""

READY_REPLY = """Here's a draft.

```ticket
Title: Add mathutil.Min helper
READY: yes

## Problem
No Min yet.

## Acceptance criteria
- Add Min(a, b int) int.
- Add a test.
```
"""


class _FakeNotifier:
    def __init__(self):
        self.thread_posts = []
        self.buttons = []

    async def post_new_draft(self, text):
        self.thread_posts.append(("root", text))
        return "ts1"

    async def post_thread_text(self, ts, text):
        self.thread_posts.append((ts, text))

    async def post_ticket_buttons(self, ts):
        self.buttons.append(ts)


class _FakeClient:
    def __init__(self):
        self.created = []

    async def create_issue(self, slug, title, description, state):
        self.created.append((slug, title, state))
        return Issue(id="new1", identifier="DEA-99", title=title,
                     url="http://x/DEA-99", state=state)


def _mo(tmp_path: Path, draft_reply=READY_REPLY):
    wf = tmp_path / "workflow.yaml"
    wf.write_text(WORKFLOW)
    mo = MultiOrchestrator(wf)
    mo.notifier = _FakeNotifier()
    client = _FakeClient()
    fake_orch = types.SimpleNamespace(
        cfg=types.SimpleNamespace(tracker=types.SimpleNamespace(project_slug="p")),
        _ensure_linear_client=lambda: client,
    )
    mo.orchestrators = {"p": fake_orch}

    async def _fake_turn(conversation):
        return draft_reply

    mo._draft_turn = _fake_turn  # type: ignore[assignment]
    return mo, client


def test_start_draft_registers_thread_and_offers_buttons(tmp_path: Path):
    mo, _ = _mo(tmp_path)
    asyncio.run(mo.start_ticket_draft("add a Min helper"))
    assert mo.is_draft_thread("ts1")                 # thread tracked
    assert mo.notifier.buttons == ["ts1"]            # ready → buttons offered
    # The draft was parsed and stored.
    assert mo._drafts["ts1"]["draft"]["title"] == "Add mathutil.Min helper"


def test_create_from_draft_files_issue_and_clears(tmp_path: Path):
    mo, client = _mo(tmp_path)
    asyncio.run(mo.start_ticket_draft("add a Min helper"))
    asyncio.run(mo.create_ticket_from_draft("ts1"))
    assert client.created == [("p", "Add mathutil.Min helper", "Todo")]
    assert not mo.is_draft_thread("ts1")             # draft cleared after creation
    assert any("DEA-99" in t for _, t in mo.notifier.thread_posts)  # link posted


def test_create_blocked_when_not_ready(tmp_path: Path):
    not_ready = "```ticket\nTitle: X\nREADY: no\n\nbody\n```"
    mo, client = _mo(tmp_path, draft_reply=not_ready)
    asyncio.run(mo.start_ticket_draft("vague idea"))
    assert mo.notifier.buttons == []                 # not ready → no buttons
    asyncio.run(mo.create_ticket_from_draft("ts1"))
    assert client.created == []                       # nothing filed
    assert mo.is_draft_thread("ts1")                  # still drafting


def test_cancel_discards_draft(tmp_path: Path):
    mo, _ = _mo(tmp_path)
    asyncio.run(mo.start_ticket_draft("add a Min helper"))
    asyncio.run(mo.cancel_ticket_draft("ts1"))
    assert not mo.is_draft_thread("ts1")
