from __future__ import annotations

from fastapi.testclient import TestClient

from ai_agent_hub.api_server import app


client = TestClient(app)


def test_post_envelopes_returns_envelope_id(monkeypatch):
    monkeypatch.setattr("ai_agent_hub.api_server.send_envelope_via_smtp", lambda env: None)

    response = client.post(
        "/envelopes",
        json={"intent": "llm-query", "text": "こんにちは"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "envelope_id" in body
    assert body["status"] == "queued"


def test_get_health_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


def test_get_approvals_pending_returns_list(monkeypatch):
    class _DummyStore:
        def list_pending(self):
            return []

    monkeypatch.setattr("ai_agent_hub.api_server.ApprovalStore", _DummyStore)

    response = client.get("/approvals/pending")

    assert response.status_code == 200
    assert isinstance(response.json(), list)
