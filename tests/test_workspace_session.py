"""Tests for agent session-id persistence in the workspace."""

from pathlib import Path

from stokowski.workspace import META_DIRNAME, read_session_id, write_session_id


def test_write_then_read(tmp_path: Path):
    write_session_id(tmp_path, "sess-abc")
    assert (tmp_path / META_DIRNAME / "session").exists()
    assert read_session_id(tmp_path) == "sess-abc"


def test_read_missing_returns_none(tmp_path: Path):
    assert read_session_id(tmp_path) is None


def test_write_empty_is_noop(tmp_path: Path):
    write_session_id(tmp_path, "")
    assert read_session_id(tmp_path) is None


def test_write_strips_whitespace(tmp_path: Path):
    write_session_id(tmp_path, "  sess-xyz \n")
    assert read_session_id(tmp_path) == "sess-xyz"
