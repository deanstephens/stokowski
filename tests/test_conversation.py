"""Tests for the conversational-gate building blocks (#8)."""

from pathlib import Path

from stokowski.config import LinearStatesConfig, StateConfig
from stokowski.models import Issue
from stokowski.prompt import build_conversation_prompt, build_lifecycle_section
from stokowski.workspace import (
    CONVERSATION_FILENAME,
    META_DIRNAME,
    append_conversation,
    read_conversation,
)


def _issue():
    return Issue(id="u1", identifier="DEA-9", title="Add Divide", url="http://x/DEA-9")


# --- transcript store --------------------------------------------------------

def test_conversation_roundtrip(tmp_path: Path):
    append_conversation(tmp_path, "human", "why an error not a panic?")
    append_conversation(tmp_path, "agent", "errors compose better in Go")
    convo = read_conversation(tmp_path)
    assert [(c["role"], c["text"]) for c in convo] == [
        ("human", "why an error not a panic?"),
        ("agent", "errors compose better in Go"),
    ]
    assert (tmp_path / META_DIRNAME / CONVERSATION_FILENAME).exists()


def test_conversation_empty_is_skipped(tmp_path: Path):
    append_conversation(tmp_path, "human", "   ")
    assert read_conversation(tmp_path) == []


def test_read_conversation_missing(tmp_path: Path):
    assert read_conversation(tmp_path) == []


def test_read_conversation_skips_bad_lines(tmp_path: Path):
    meta = tmp_path / META_DIRNAME
    meta.mkdir(parents=True)
    (meta / CONVERSATION_FILENAME).write_text(
        '{"role":"human","text":"ok"}\nnot json\n{"role":"agent","text":"hi"}\n'
    )
    convo = read_conversation(tmp_path)
    assert [c["text"] for c in convo] == ["ok", "hi"]


# --- conversation prompt -----------------------------------------------------

def test_build_conversation_prompt_contains_message_and_guardrails():
    p = build_conversation_prompt(
        _issue(),
        "can you explain the divide-by-zero handling?",
        history=[{"role": "human", "text": "earlier q"}, {"role": "agent", "text": "earlier a"}],
    )
    assert "DEA-9" in p
    assert "can you explain the divide-by-zero handling?" in p
    assert "MUST NOT modify" in p          # read-only guardrail
    assert "earlier q" in p and "earlier a" in p   # history included


def test_build_conversation_prompt_truncates_history():
    history = [{"role": "human", "text": f"msg{i}"} for i in range(30)]
    p = build_conversation_prompt(_issue(), "latest", history=history)
    assert "msg29" in p          # recent kept
    assert "msg0" not in p       # old dropped (last 10 only)


# --- lifecycle injection -----------------------------------------------------

def test_lifecycle_includes_conversation_section():
    sc = StateConfig(name="implement", type="agent", transitions={"complete": "review"})
    out = build_lifecycle_section(
        _issue(), "implement", sc, LinearStatesConfig(),
        conversation=[
            {"role": "human", "text": "please rename Foo to Bar"},
            {"role": "agent", "text": "will do on rework"},
        ],
    )
    assert "Discussion at the review gate" in out
    assert "please rename Foo to Bar" in out
    assert "will do on rework" in out


def test_lifecycle_no_conversation_section_when_empty():
    sc = StateConfig(name="implement", type="agent")
    out = build_lifecycle_section(_issue(), "implement", sc, LinearStatesConfig())
    assert "Discussion at the review gate" not in out
