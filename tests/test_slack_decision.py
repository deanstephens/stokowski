"""Tests for attributed, visually-separated gate decisions (#12)."""

import asyncio

from stokowski.slack import SlackNotifier


def _notifier():
    n = SlackNotifier("tok", "C1")
    # Capture posts instead of hitting the network.
    n.posts = []

    async def _fake_post(text, blocks=None, thread_ts=None):
        n.posts.append({"text": text, "blocks": blocks or [], "thread_ts": thread_ts})
        return "ts.posted"

    n._post_message = _fake_post  # type: ignore[assignment]
    return n


def _run(coro):
    return asyncio.run(coro)


def _texts(blocks):
    return " ".join(
        b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section"
    )


def _has_divider(blocks):
    return any(b.get("type") == "divider" for b in blocks)


def test_rework_with_actor_mention():
    n = _notifier()
    n._issue_thread["i1"] = "ts1"
    _run(n.post_gate_decision("i1", "rework", actor_uid="U123", actor_name="alice"))
    post = n.posts[-1]
    assert post["thread_ts"] == "ts1"
    assert _has_divider(post["blocks"])
    body = _texts(post["blocks"])
    assert "Sent back for rework" in body
    assert "<@U123>" in body  # @-mention preferred over name


def test_approve_with_actor_mention():
    n = _notifier()
    n._issue_thread["i1"] = "ts1"
    _run(n.post_gate_decision("i1", "approve", actor_uid="U999", actor_name="bob"))
    body = _texts(n.posts[-1]["blocks"])
    assert "Approved" in body and "<@U999>" in body
    assert _has_divider(n.posts[-1]["blocks"])


def test_actor_name_fallback_without_uid():
    n = _notifier()
    n._issue_thread["i1"] = "ts1"
    _run(n.post_gate_decision("i1", "rework", actor_name="carol"))
    body = _texts(n.posts[-1]["blocks"])
    assert "by carol" in body and "<@" not in body


def test_no_actor_still_separates_without_author():
    n = _notifier()
    n._issue_thread["i1"] = "ts1"
    _run(n.post_gate_decision("i1", "rework"))
    body = _texts(n.posts[-1]["blocks"])
    assert "Sent back for rework" in body
    assert "by" not in body
    assert _has_divider(n.posts[-1]["blocks"])


def test_failed_decision_warns():
    n = _notifier()
    n._issue_thread["i1"] = "ts1"
    _run(n.post_gate_decision("i1", "approve", actor_uid="U1", ok=False))
    assert "Could not apply" in _texts(n.posts[-1]["blocks"])


def test_no_thread_no_post():
    n = _notifier()
    _run(n.post_gate_decision("unknown", "rework", actor_uid="U1"))
    assert n.posts == []


def test_record_actor_adds_participant():
    n = _notifier()
    n._issue_thread["i1"] = "ts1"
    n.record_actor("i1", "U777")
    assert n._thread_participants["ts1"] == {"U777"}


def test_record_actor_no_thread_is_safe():
    n = _notifier()
    n.record_actor("missing", "U1")  # must not raise
    assert n._thread_participants == {}
