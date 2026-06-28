"""Tests for linking the Linear card back to the Slack thread."""

import asyncio

from stokowski.models import Issue
from stokowski.slack import SlackNotifier


def _notifier(permalink="https://slack.example/archives/C1/p123"):
    n = SlackNotifier("tok", "C1")
    n.posts = []

    async def _fake_post(text, blocks=None, thread_ts=None):
        n.posts.append({"text": text, "thread_ts": thread_ts})
        return "ts.123"

    async def _fake_permalink(ts):
        return permalink

    n._post_message = _fake_post          # type: ignore[assignment]
    n._permalink = _fake_permalink        # type: ignore[assignment]
    return n


def _issue():
    return Issue(id="i1", identifier="DEA-1", title="t", url="http://x/DEA-1")


def test_permalink_callback_invoked_on_first_gate():
    n = _notifier()
    got = []

    async def cb(link):
        got.append(link)

    asyncio.run(n.notify_gate(_issue(), "review", run=1, on_permalink=cb))
    assert got == ["https://slack.example/archives/C1/p123"]


def test_no_callback_when_not_provided():
    n = _notifier()
    # Should not raise when on_permalink is omitted.
    asyncio.run(n.notify_gate(_issue(), "review", run=1))
    assert n._issue_thread["i1"] == "ts.123"


def test_no_duplicate_link_on_rereview():
    # Re-review continues the existing thread (no new ts) → no second link.
    n = _notifier()
    n._issue_thread["i1"] = "tsA"
    got = []

    async def cb(link):
        got.append(link)

    asyncio.run(n.notify_gate(_issue(), "review", run=2, on_permalink=cb))
    assert got == []  # callback not fired on the in-thread re-review


def test_no_link_when_permalink_unavailable():
    n = _notifier(permalink=None)
    got = []

    async def cb(link):
        got.append(link)

    asyncio.run(n.notify_gate(_issue(), "review", run=1, on_permalink=cb))
    assert got == []  # nothing to link if the permalink lookup failed
