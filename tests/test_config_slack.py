"""Tests for SlackConfig / ServerConfig auth parsing and env resolution."""

from pathlib import Path

from stokowski.config import ServerConfig, SlackConfig, parse_workflow_file


def test_slack_config_env_resolution(monkeypatch):
    monkeypatch.setenv("MY_BOT", "xoxb-secret")
    monkeypatch.setenv("MY_CHAN", "C123")
    cfg = SlackConfig(
        enabled=True, bot_token="$MY_BOT", channel="$MY_CHAN", signing_secret="lit"
    )
    assert cfg.resolved_bot_token() == "xoxb-secret"
    assert cfg.resolved_channel() == "C123"
    assert cfg.resolved_signing_secret() == "lit"
    assert cfg.is_active()


def test_slack_config_inactive_without_token():
    cfg = SlackConfig(enabled=True, bot_token="", channel="C1")
    assert not cfg.is_active()


def test_slack_config_disabled():
    cfg = SlackConfig(enabled=False, bot_token="t", channel="C1")
    assert not cfg.is_active()


def test_slack_config_wants():
    cfg = SlackConfig(events=["gates", "done"])
    assert cfg.wants("gates")
    assert cfg.wants("done")
    assert not cfg.wants("errors")


def test_server_auth_token_env_fallback(monkeypatch):
    monkeypatch.delenv("STOKOWSKI_AUTH_TOKEN", raising=False)
    sc = ServerConfig()
    assert sc.resolved_auth_token() == ""
    monkeypatch.setenv("STOKOWSKI_AUTH_TOKEN", "envtok")
    assert ServerConfig().resolved_auth_token() == "envtok"


def test_server_auth_token_explicit_env_ref(monkeypatch):
    monkeypatch.setenv("DASH_TOK", "abc123")
    sc = ServerConfig(auth_token="$DASH_TOK")
    assert sc.resolved_auth_token() == "abc123"


SAMPLE_WORKFLOW = """
tracker:
  kind: linear
  api_key: $LINEAR_API_KEY
  project_slug: my-proj
server:
  port: 8080
  auth_token: $STOKOWSKI_AUTH_TOKEN
slack:
  enabled: true
  bot_token: $SLACK_BOT_TOKEN
  signing_secret: $SLACK_SIGNING_SECRET
  channel: C0001
  events:
    - gates
    - errors
states:
  build:
    type: agent
    linear_state: active
"""


def test_parse_workflow_reads_slack_and_auth(tmp_path: Path):
    wf = tmp_path / "workflow.yaml"
    wf.write_text(SAMPLE_WORKFLOW)
    parsed = parse_workflow_file(wf)
    cfg = parsed.config
    assert cfg.server.port == 8080
    assert cfg.server.auth_token == "$STOKOWSKI_AUTH_TOKEN"
    assert cfg.slack.enabled is True
    assert cfg.slack.channel == "C0001"
    assert cfg.slack.events == ["gates", "errors"]
    assert cfg.slack.bot_token == "$SLACK_BOT_TOKEN"


def test_parse_workflow_slack_defaults(tmp_path: Path):
    wf = tmp_path / "workflow.yaml"
    wf.write_text(
        "tracker:\n  kind: linear\n  api_key: k\n  project_slug: p\n"
        "states:\n  build:\n    type: agent\n"
    )
    cfg = parse_workflow_file(wf).config
    # Defaults: disabled, all three event categories.
    assert cfg.slack.enabled is False
    assert cfg.slack.events == ["gates", "errors", "done"]
    assert cfg.server.auth_token == ""
