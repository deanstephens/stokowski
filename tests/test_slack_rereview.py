"""Tests for re-review thread continuity (#14)."""

import asyncio

from stokowski.models import Issue
from stokowski.slack import SlackNotifier


def _notifier():
    n = SlackNotifier("tok", "C1")  # mentions off → no network lookups
    n.posts = []

    async def _fake_post(text, blocks=None, thread_ts=None):
        n.posts.append({"text": text, "blocks": blocks or [], "thread_ts": thread_ts})
        return "ts.new"

    n._post_message = _fake_post  # type: ignore[assignment]
    return n


def _issue():
    return Issue(id="i1", identifier="DEA-1", title="Thing", url="http://x/DEA-1")


def _section_text(blocks):
    return " ".join(
        b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section"
    )


def test_first_review_starts_new_thread():
    n = _notifier()
    asyncio.run(n.notify_gate(_issue(), "review", run=1))
    p = n.posts[-1]
    assert p["thread_ts"] is None  # top-level message
    assert n._issue_thread["i1"] == "ts.new"
    assert n._thread_issue["ts.new"] == "i1"


def test_rereview_posts_in_existing_thread():
    n = _notifier()
    n._issue_thread["i1"] = "tsA"
    n._thread_issue["tsA"] = "i1"
    asyncio.run(n.notify_gate(_issue(), "review", run=2))
    p = n.posts[-1]
    assert p["thread_ts"] == "tsA"        # continues original thread, not orphaned
    assert n._issue_thread["i1"] == "tsA"  # mapping preserved
    body = _section_text(p["blocks"])
    assert "Back for review after rework" in body
    assert "run 2" in body
    assert any(b.get("type") == "divider" for b in p["blocks"])
    # Buttons are still present on the in-thread re-review message.
    assert any(b.get("type") == "actions" for b in p["blocks"])


def test_rereview_without_thread_falls_back_to_new_message():
    n = _notifier()
    # run > 1 but no remembered thread (e.g. after an orchestrator restart).
    asyncio.run(n.notify_gate(_issue(), "review", run=2))
    p = n.posts[-1]
    assert p["thread_ts"] is None
    assert n._issue_thread["i1"] == "ts.new"
