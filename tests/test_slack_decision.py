"""Tests for attributed, visually-separated gate decisions (#12, #16)."""

import asyncio

from stokowski.models import Issue
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


# --- #16: reflect decisions from any origin, without double-posting ----------

def test_neutral_source_when_no_actor():
    # Linear-originated decision (no Slack actor) → uses the neutral source.
    n = _notifier()
    n._issue_thread["i1"] = "ts1"
    _run(n.post_gate_decision("i1", "approve", source="in Linear"))
    body = _texts(n.posts[-1]["blocks"])
    assert "Approved" in body and "in Linear" in body and "<@" not in body


def test_decision_announced_only_once():
    # Slack-button path posts with actor; the orchestrator's later call for the
    # same gate round is a no-op (dedupe).
    n = _notifier()
    n._issue_thread["i1"] = "ts1"
    _run(n.post_gate_decision("i1", "approve", actor_uid="U1"))  # button
    _run(n.post_gate_decision("i1", "approve", source="in Linear"))  # orchestrator
    assert len(n.posts) == 1
    assert "<@U1>" in _texts(n.posts[-1]["blocks"])  # the attributed one won


def test_linear_only_decision_posts_once():
    n = _notifier()
    n._issue_thread["i1"] = "ts1"
    _run(n.post_gate_decision("i1", "rework", source="in Linear"))
    _run(n.post_gate_decision("i1", "rework", source="in Linear"))
    assert len(n.posts) == 1


def test_new_gate_round_allows_next_decision():
    # After a decision, re-entering the gate must reset the dedupe marker so the
    # next round's decision is announced again.
    n = _notifier()
    n._issue_thread["i1"] = "ts1"
    _run(n.post_gate_decision("i1", "rework", source="in Linear"))
    assert len(n.posts) == 1
    # Re-review re-enters the gate (run 2), which resets the marker.
    issue = Issue(id="i1", identifier="DEA-1", title="t", url="http://x")
    _run(n.notify_gate(issue, "review", run=2))
    _run(n.post_gate_decision("i1", "approve", source="in Linear"))
    decisions = [p for p in n.posts if "Approved" in _texts(p["blocks"])]
    assert len(decisions) == 1  # the new round's approve was announced
