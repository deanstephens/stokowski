"""Tests for conversational ticket drafting (#19)."""

from stokowski.drafting import build_draft_prompt, parse_draft

READY_REPLY = """Sounds good — here's a draft.

```ticket
Title: Add mathutil.Min helper
READY: yes

## Problem
The package has Max but no Min.

## Acceptance criteria
- Add `func Min(a, b int) int`.
- Add a passing test.
```

Anything to change?"""

NOT_READY_REPLY = """A couple of questions first.

```ticket
Title: Add a Min helper
READY: no

## Problem
TBD — need to confirm the signature.
```
"""


def test_parse_ready_draft():
    d = parse_draft(READY_REPLY)
    assert d["title"] == "Add mathutil.Min helper"
    assert "Acceptance criteria" in d["description"]
    assert "READY" not in d["description"]  # marker line stripped from body
    assert "Title:" not in d["description"]
    assert d["ready"] is True


def test_parse_not_ready_draft():
    d = parse_draft(NOT_READY_REPLY)
    assert d["title"] == "Add a Min helper"
    assert d["ready"] is False


def test_ready_requires_body():
    text = "```ticket\nTitle: X\nREADY: yes\n```"
    d = parse_draft(text)
    assert d["title"] == "X"
    assert d["ready"] is False  # no description body → not ready


def test_no_block_returns_empty():
    d = parse_draft("just chatting, no draft here")
    assert d == {"title": "", "description": "", "ready": False}


def test_ready_flag_is_case_insensitive():
    text = "```TICKET\nTitle: Y\nReady: YES\n\nbody\n```"
    d = parse_draft(text)
    assert d["title"] == "Y" and d["ready"] is True


def test_build_prompt_includes_system_and_conversation():
    convo = [
        {"role": "user", "text": "add a Min helper"},
        {"role": "assistant", "text": "what should Min(equal) return?"},
        {"role": "user", "text": "either is fine"},
    ]
    p = build_draft_prompt(convo)
    assert "engineering ticket" in p          # from DRAFT_SYSTEM
    assert "```ticket" in p                    # format instructions present
    assert "User: add a Min helper" in p
    assert "You: what should Min(equal) return?" in p
    assert p.rstrip().endswith("```ticket block.")
