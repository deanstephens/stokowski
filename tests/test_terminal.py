"""Unit tests for tmux session helpers."""

import shutil
import tempfile
from pathlib import Path

import pytest

from stokowski.terminal import (
    SESSION_PREFIX,
    ensure_session,
    has_session,
    kill_session,
    session_name,
)

tmux_missing = shutil.which("tmux") is None


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


@pytest.mark.skipif(tmux_missing, reason="tmux not installed")
def test_ensure_session_replaces_stale_workspace():
    """A session tagged for an old workspace is replaced, not reused blindly."""
    import asyncio

    from stokowski.terminal import _session_workspace

    ident = "STALE-TEST-1"
    ws_a = Path(tempfile.mkdtemp(prefix="stok-a-"))
    ws_b = Path(tempfile.mkdtemp(prefix="stok-b-"))

    async def scenario():
        await kill_session(ident)
        name = await ensure_session(ident, ws_a)
        assert await has_session(ident)
        assert await _session_workspace(name) == str(ws_a.resolve())
        # Re-ensuring with a different workspace must rebind to ws_b.
        name2 = await ensure_session(ident, ws_b)
        assert name2 == name
        assert await _session_workspace(name) == str(ws_b.resolve())

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(kill_session(ident))
        shutil.rmtree(ws_a, ignore_errors=True)
        shutil.rmtree(ws_b, ignore_errors=True)
