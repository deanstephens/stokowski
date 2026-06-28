"""Tests for clearing gate buttons when the card is decided/moved (#21)."""

import asyncio

from stokowski.models import Issue
from stokowski.slack import SlackNotifier


def _notifier():
    n = SlackNotifier("tok", "C1")
    n.posts = []
    n.updates = []

    async def _fake_post(text, blocks=None, thread_ts=None):
        n.posts.append({"text": text, "blocks": blocks or [], "thread_ts": thread_ts})
        return "ts.gate"

    async def _fake_update(ts, blocks, text="Review updated"):
        n.updates.append({"ts": ts, "blocks": blocks})

    n._post_message = _fake_post      # type: ignore[assignment]
    n._update_message = _fake_update  # type: ignore[assignment]
    return n


def _issue():
    return Issue(id="i1", identifier="DEA-1", title="t", url="http://x/DEA-1")


def _has_actions(blocks):
    return any(b.get("type") == "actions" for b in blocks)


def test_gate_message_tracked_with_buttons():
    n = _notifier()
    asyncio.run(n.notify_gate(_issue(), "review", run=1))
    assert "i1" in n._gate_message
    assert _has_actions(n._gate_message["i1"]["blocks"])  # buttons present initially


def test_clear_removes_actions_block_and_adds_note():
    n = _notifier()
    asyncio.run(n.notify_gate(_issue(), "review", run=1))
    asyncio.run(n.clear_gate_buttons("i1", note=":white_check_mark: Approved."))
    assert len(n.updates) == 1
    new_blocks = n.updates[-1]["blocks"]
    assert not _has_actions(new_blocks)              # buttons gone
    assert n.updates[-1]["ts"] == "ts.gate"          # edited the gate message
    assert any(b.get("type") == "context" for b in new_blocks)  # note appended
    assert "i1" not in n._gate_message               # no longer tracked


def test_clear_is_idempotent_noop_when_untracked():
    n = _notifier()
    asyncio.run(n.clear_gate_buttons("missing"))
    assert n.updates == []                            # nothing to update


def test_clear_twice_only_updates_once():
    n = _notifier()
    asyncio.run(n.notify_gate(_issue(), "review", run=1))
    asyncio.run(n.clear_gate_buttons("i1"))
    asyncio.run(n.clear_gate_buttons("i1"))
    assert len(n.updates) == 1                        # second call is a no-op


def test_rereview_tracks_new_button_message():
    n = _notifier()
    n._issue_thread["i1"] = "tsA"  # existing thread → re-review path
    asyncio.run(n.notify_gate(_issue(), "review", run=2))
    # The re-review message (ts.gate from the fake) now holds the live buttons.
    assert n._gate_message["i1"]["ts"] == "ts.gate"
    assert _has_actions(n._gate_message["i1"]["blocks"])
