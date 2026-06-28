"""Tests for dashboard bearer-token auth and Slack-route exemption."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from stokowski.web import create_app  # noqa: E402


class _StubOrchestrator:
    """Minimal stand-in exposing only what create_app's routes touch."""

    notifier = None  # Slack disabled -> signature check fails -> 401

    def get_state_snapshot(self):
        return {
            "running": [], "retrying": [], "gates": [], "queued": [],
            "counts": {}, "totals": {}, "projects": [],
        }

    @property
    def project_names(self):
        return []


def _client(auth_token=""):
    return TestClient(create_app(_StubOrchestrator(), auth_token=auth_token))


def test_healthz_open_without_token():
    c = _client(auth_token="secret")
    assert c.get("/healthz").status_code == 200


def test_state_requires_token_when_auth_enabled():
    c = _client(auth_token="secret")
    assert c.get("/api/v1/state").status_code == 401


def test_state_accepts_query_token():
    c = _client(auth_token="secret")
    assert c.get("/api/v1/state?token=secret").status_code == 200


def test_state_accepts_bearer_header():
    c = _client(auth_token="secret")
    r = c.get("/api/v1/state", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200


def test_state_rejects_wrong_token():
    c = _client(auth_token="secret")
    assert c.get("/api/v1/state?token=nope").status_code == 401


def test_no_auth_when_token_unset():
    c = _client(auth_token="")
    assert c.get("/api/v1/state").status_code == 200


def test_dashboard_injects_token():
    c = _client(auth_token="secret")
    r = c.get("/?token=secret")
    assert r.status_code == 200
    assert '"secret"' in r.text  # window.STOK_TOKEN = "secret";


def test_slack_route_exempt_from_bearer_but_needs_signature():
    # Exempt from bearer auth (no 401 for missing token) but rejected for a
    # bad/missing Slack signature (notifier is None -> empty signing secret).
    c = _client(auth_token="secret")
    r = c.post("/slack/events", content=b"{}")
    assert r.status_code == 401
    assert "signature" in r.text.lower()
