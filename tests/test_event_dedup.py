"""Tests for Slack event de-duplication (#23)."""

from stokowski.web import _EventDedup


def test_first_sighting_not_duplicate():
    d = _EventDedup()
    assert d.is_duplicate("Ev1") is False


def test_repeat_is_duplicate():
    d = _EventDedup()
    d.is_duplicate("Ev1")
    assert d.is_duplicate("Ev1") is True
    assert d.is_duplicate("Ev1") is True  # stays duplicate


def test_distinct_ids_not_duplicate():
    d = _EventDedup()
    assert d.is_duplicate("Ev1") is False
    assert d.is_duplicate("Ev2") is False


def test_missing_id_never_duplicate():
    d = _EventDedup()
    assert d.is_duplicate(None) is False
    assert d.is_duplicate("") is False
    assert d.is_duplicate(None) is False  # no state accumulated


def test_eviction_keeps_set_in_sync():
    d = _EventDedup(maxlen=2)
    d.is_duplicate("a")
    d.is_duplicate("b")
    d.is_duplicate("c")  # evicts "a"
    assert d.is_duplicate("a") is False  # "a" was evicted → seen as new again
    assert d.is_duplicate("c") is True   # "c" still remembered
    assert len(d._set) == len(d._q)      # set and queue stay in sync
