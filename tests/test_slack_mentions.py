"""Tests for targeted Slack mentions (#10): creator + thread participants."""

import asyncio

from stokowski.slack import SlackNotifier


def _notifier(mentions=True, user_map=None):
    # user_map lets us resolve emails without hitting the Slack API.
    return SlackNotifier("tok", "C1", mentions=mentions, user_map=user_map or {})


def _mentions_for(n, issue_id, exclude=None):
    return asyncio.run(n.mentions_for(issue_id, exclude=exclude))


def test_disabled_returns_empty():
    n = _notifier(mentions=False, user_map={"c@x.com": "UC"})
    n._issue_creator["i1"] = "c@x.com"
    assert _mentions_for(n, "i1") == ""


def test_creator_resolved_via_user_map():
    n = _notifier(user_map={"c@x.com": "UC"})
    n._issue_creator["i1"] = "c@x.com"
    assert _mentions_for(n, "i1") == "<@UC> "


def test_user_map_is_case_insensitive():
    n = _notifier(user_map={"c@x.com": "UC"})
    n._issue_creator["i1"] = "C@X.com"  # different case than the map key
    assert _mentions_for(n, "i1") == "<@UC> "


def test_participants_pinged_with_creator():
    n = _notifier(user_map={"c@x.com": "UC"})
    n._issue_creator["i1"] = "c@x.com"
    n._issue_thread["i1"] = "ts1"
    n.record_participant("ts1", "UP1")
    n.record_participant("ts1", "UP2")
    out = _mentions_for(n, "i1")
    assert "<@UC> " in out and "<@UP1> " in out and "<@UP2> " in out
    # Creator comes first.
    assert out.startswith("<@UC> ")


def test_dedup_when_creator_also_replied():
    n = _notifier(user_map={"c@x.com": "UC"})
    n._issue_creator["i1"] = "c@x.com"
    n._issue_thread["i1"] = "ts1"
    n.record_participant("ts1", "UC")  # creator also replied in-thread
    assert _mentions_for(n, "i1").count("<@UC>") == 1


def test_exclude_filters_out_user():
    n = _notifier(user_map={"c@x.com": "UC"})
    n._issue_creator["i1"] = "c@x.com"
    assert _mentions_for(n, "i1", exclude={"UC"}) == ""


def test_empty_when_nothing_resolves():
    n = _notifier()
    assert _mentions_for(n, "unknown") == ""


def test_record_participant_ignores_blanks():
    n = _notifier()
    n.record_participant("", "U1")
    n.record_participant("ts", "")
    assert n._thread_participants == {}
