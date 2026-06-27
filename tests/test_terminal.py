"""Unit tests for tmux session-name derivation."""

from stokowski.terminal import SESSION_PREFIX, session_name


def test_session_name_prefixed():
    assert session_name("SYN-42").startswith(SESSION_PREFIX)


def test_session_name_sanitizes_unsafe_chars():
    # tmux forbids '.' and ':' in session names; spaces/slashes are unsafe dirs.
    name = session_name("team/proj:1.2 (x)")
    assert "." not in name
    assert ":" not in name
    assert " " not in name
    assert "/" not in name


def test_session_name_stable():
    assert session_name("SYN-42") == session_name("SYN-42")
