from __future__ import annotations

import json
from fastapi.testclient import TestClient

import ai_agent_hub.api_server as api_server
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
        def __init__(self, *args, **kwargs):
            pass

        def list_pending(self):
            return []

    monkeypatch.setattr("ai_agent_hub.api_server.ApprovalStore", _DummyStore)

    response = client.get("/approvals/pending")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_get_logs_returns_log_list(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "PROCESSED_DIR", tmp_path)

    env_data = {
        "id": "env-1",
        "time": "2026-04-04T13:58:33Z",
        "from": "https://user.local/@me",
        "to": "https://agent.local/@worker",
        "type": "email",
        "payload": {"intent": "ping", "pong": True},
        "context": "tx_9987",
        "inReplyTo": None,
    }
    (tmp_path / "env-1.json").write_text(json.dumps(env_data), encoding="utf-8")

    response = client.get("/logs")

    assert response.status_code == 200
    body = response.json()
    assert "logs" in body
    assert body["total"] == 1
    assert body["logs"][0]["id"] == "env-1"
    assert body["logs"][0]["time"] == "2026-04-04T13:58:33Z"
    assert body["logs"][0]["from"] == "https://user.local/@me"
    assert body["logs"][0]["to"] == "https://agent.local/@worker"
    assert body["logs"][0]["type"] == "email"
    assert body["logs"][0]["intent"] == "ping"


def test_get_logs_honors_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "PROCESSED_DIR", tmp_path)

    for i in range(7):
        env_data = {
            "id": f"env-{i}",
            "created_at": f"2026-04-04T13:58:{i:02d}Z",
            "sender": "https://user.local/@me",
            "recipient": "https://agent.local/@worker",
            "envelope_type": "command",
            "payload": {"intent": "echo", "text": f"msg-{i}"},
            "context": "tx-limit",
            "in_reply_to": None,
        }
        file_path = tmp_path / f"env-{i}.json"
        file_path.write_text(json.dumps(env_data), encoding="utf-8")

    response = client.get("/logs?limit=5")

    assert response.status_code == 200
    body = response.json()
    assert len(body["logs"]) == 5
    assert body["total"] == 7


def test_get_logs_filters_by_intent(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "PROCESSED_DIR", tmp_path)

    entries = [
        {"id": "env-ping", "intent": "ping"},
        {"id": "env-echo", "intent": "echo"},
        {"id": "env-ping-2", "intent": "ping"},
    ]
    for item in entries:
        env_data = {
            "id": item["id"],
            "created_at": "2026-04-04T13:58:33Z",
            "sender": "https://user.local/@me",
            "recipient": "https://agent.local/@worker",
            "envelope_type": "command",
            "payload": {"intent": item["intent"]},
            "context": "tx-intent",
            "in_reply_to": None,
        }
        (tmp_path / f"{item['id']}.json").write_text(json.dumps(env_data), encoding="utf-8")

    response = client.get("/logs?intent=ping")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["logs"]) == 2
    assert all(log["intent"] == "ping" for log in body["logs"])


def test_approval_endpoints_use_env_db_path(monkeypatch):
    captured: dict[str, str | None] = {}

    class _DummyStore:
        def __init__(self, db_path=None):
            captured["db_path"] = db_path

        def list_pending(self):
            return []

    monkeypatch.setenv("AI_AGENT_HUB_APPROVAL_DB", "/tmp/shared-approvals.db")
    monkeypatch.setattr("ai_agent_hub.api_server.ApprovalStore", _DummyStore)

    response = client.get("/approvals/pending")

    assert response.status_code == 200
    assert captured["db_path"] == "/tmp/shared-approvals.db"
