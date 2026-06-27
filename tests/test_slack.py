"""Unit tests for the Slack integration's pure logic."""

import hashlib
import hmac

from stokowski.models import Issue
from stokowski.slack import (
    ACTION_APPROVE,
    ACTION_REWORK,
    build_gate_blocks,
    decode_action_value,
    encode_action_value,
    verify_slack_signature,
)


def _issue():
    return Issue(
        id="uuid-123",
        identifier="SYN-42",
        title="Refactor auth",
        url="https://linear.app/x/issue/SYN-42",
    )


def test_action_value_roundtrip():
    val = encode_action_value("uuid-123", "review_gate", 2)
    ctx = decode_action_value(val)
    assert ctx == {"issue": "uuid-123", "gate": "review_gate", "run": 2}


def test_decode_action_value_garbage():
    assert decode_action_value("not json") == {}
    assert decode_action_value("") == {}


def test_build_gate_blocks_has_buttons_and_question():
    text, blocks = build_gate_blocks(
        _issue(), "review_gate", "Please review the diff", 1, question="Should I use JWT?"
    )
    assert "SYN-42" in text
    # Find the actions block and its button action_ids.
    actions = [b for b in blocks if b["type"] == "actions"]
    assert len(actions) == 1
    action_ids = {el["action_id"] for el in actions[0]["elements"]}
    assert ACTION_APPROVE in action_ids
    assert ACTION_REWORK in action_ids
    # The agent's question is rendered somewhere in the blocks.
    rendered = str(blocks)
    assert "Should I use JWT?" in rendered
    assert "Please review the diff" in rendered


def test_build_gate_blocks_button_value_decodes():
    _, blocks = build_gate_blocks(_issue(), "gate_x", "", 3)
    actions = [b for b in blocks if b["type"] == "actions"][0]
    approve = [e for e in actions["elements"] if e["action_id"] == ACTION_APPROVE][0]
    ctx = decode_action_value(approve["value"])
    assert ctx["issue"] == "uuid-123"
    assert ctx["gate"] == "gate_x"
    assert ctx["run"] == 3


def _sign(secret: str, ts: str, body: bytes) -> str:
    base = b"v0:" + ts.encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def test_verify_slack_signature_valid():
    secret = "topsecret"
    ts = "1700000000"
    body = b"payload=stuff"
    sig = _sign(secret, ts, body)
    assert verify_slack_signature(secret, ts, body, sig, now=1700000010)


def test_verify_slack_signature_tampered_body():
    secret = "topsecret"
    ts = "1700000000"
    sig = _sign(secret, ts, b"payload=stuff")
    assert not verify_slack_signature(secret, ts, b"payload=EVIL", sig, now=1700000010)


def test_verify_slack_signature_replay_rejected():
    secret = "topsecret"
    ts = "1700000000"
    body = b"x"
    sig = _sign(secret, ts, body)
    # 10 minutes later — outside the 5-minute window.
    assert not verify_slack_signature(secret, ts, body, sig, now=1700000000 + 600)


def test_verify_slack_signature_missing_inputs():
    assert not verify_slack_signature("", "1", b"x", "v0=abc")
    assert not verify_slack_signature("s", "", b"x", "v0=abc")
    assert not verify_slack_signature("s", "1", b"x", "")
