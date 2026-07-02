from __future__ import annotations

import json
from fastapi.testclient import TestClient

import ai_agent_hub.api_server as api_server
from ai_agent_hub.api_server import app


client = TestClient(app)


def test_post_envelopes_returns_envelope_id(monkeypatch):
    monkeypatch.setattr("ai_agent_hub.api_server.save_envelope", lambda env: None)

    response = client.post(
        "/envelopes",
        json={"intent": "llm-query", "text": "こんにちは"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "envelope_id" in body
    assert body["status"] == "queued"


def test_post_envelopes_accepts_answers_payload(monkeypatch):
    captured = {}

    def _capture_env(env):
        captured["payload"] = env.payload

    monkeypatch.setattr("ai_agent_hub.api_server.save_envelope", _capture_env)

    response = client.post(
        "/envelopes",
        json={
            "intent": "cat-assessment",
            "answers": {"living_situation": "1LDK"},
        },
    )

    assert response.status_code == 200
    assert captured["payload"]["intent"] == "cat-assessment"
    assert captured["payload"]["answers"] == {"living_situation": "1LDK"}


def test_post_envelopes_accepts_nested_payload(monkeypatch):
    captured = {}

    def _capture_env(env):
        captured["payload"] = env.payload

    monkeypatch.setattr("ai_agent_hub.api_server.save_envelope", _capture_env)

    response = client.post(
        "/envelopes",
        json={
            "intent": "cat-assessment",
            "payload": {"answers": {"living_situation": "1LDK"}},
        },
    )

    assert response.status_code == 200
    assert captured["payload"]["intent"] == "cat-assessment"
    assert captured["payload"]["answers"] == {"living_situation": "1LDK"}


def test_post_envelopes_preserves_payload_fields(monkeypatch):
    captured = {}

    def _capture_env(env):
        captured["payload"] = env.payload

    monkeypatch.setattr("ai_agent_hub.api_server.save_envelope", _capture_env)

    response = client.post(
        "/envelopes",
        json={
            "intent": "threat-scan",
            "payload": {
                "keywords": ["cat abuse"],
                "languages": ["ja", "en"],
                "sector": "TOKYO-SECTOR",
            },
        },
    )

    assert response.status_code == 200
    assert captured["payload"]["intent"] == "threat-scan"
    assert captured["payload"]["keywords"] == ["cat abuse"]
    assert captured["payload"]["languages"] == ["ja", "en"]
    assert captured["payload"]["sector"] == "TOKYO-SECTOR"


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


def test_post_envelopes_request_approval_payload_and_thread(monkeypatch):
    captured = {}

    def _capture_env(env):
        captured["payload"] = env.payload
        captured["context"] = env.context

    monkeypatch.setattr("ai_agent_hub.api_server.save_envelope", _capture_env)

    response = client.post(
        "/envelopes",
        json={
            "intent": "request-approval",
            "description": "経費申請",
            "approver": "https://company.local/@manager",
            "callback_payload": {"intent": "echo", "text": "承認されました"},
            "thread_id": "thread-1",
        },
    )

    assert response.status_code == 200
    assert captured["payload"] == {
        "intent": "request-approval",
        "description": "経費申請",
        "approver": "https://company.local/@manager",
        "callback_payload": {"intent": "echo", "text": "承認されました"},
    }
    assert captured["context"] == "thread-1"


def test_post_envelopes_request_approval_parses_text_json_fallback(monkeypatch):
    captured = {}

    def _capture_env(env):
        captured["payload"] = env.payload

    monkeypatch.setattr("ai_agent_hub.api_server.save_envelope", _capture_env)

    response = client.post(
        "/envelopes",
        json={
            "intent": "request-approval",
            "text": (
                '{"description":"text fallback",'
                '"approver":"https://company.local/@manager",'
                '"callback_payload":{"intent":"echo"}}'
            ),
        },
    )

    assert response.status_code == 200
    assert captured["payload"] == {
        "intent": "request-approval",
        "text": (
            '{"description":"text fallback",'
            '"approver":"https://company.local/@manager",'
            '"callback_payload":{"intent":"echo"}}'
        ),
        "description": "text fallback",
        "approver": "https://company.local/@manager",
        "callback_payload": {"intent": "echo"},
    }


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


def test_get_logs_honors_offset(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "PROCESSED_DIR", tmp_path)

    for i in range(3):
        env_data = {
            "id": f"env-{i}",
            "created_at": f"2026-04-04T13:58:{i:02d}Z",
            "sender": "https://user.local/@me",
            "recipient": "https://agent.local/@worker",
            "envelope_type": "command",
            "payload": {"intent": "echo", "index": i},
            "context": "tx-offset",
            "in_reply_to": None,
        }
        (tmp_path / f"env-{i}.json").write_text(json.dumps(env_data), encoding="utf-8")

    response = client.get("/logs?limit=1&offset=1")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["logs"]) == 1


def test_get_logs_filters_by_since_until(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "PROCESSED_DIR", tmp_path)

    entries = [
        ("env-old", "2026-04-04T10:00:00Z"),
        ("env-mid", "2026-04-04T11:00:00Z"),
        ("env-new", "2026-04-04T12:00:00Z"),
    ]
    for env_id, created_at in entries:
        env_data = {
            "id": env_id,
            "created_at": created_at,
            "sender": "https://user.local/@me",
            "recipient": "https://agent.local/@worker",
            "envelope_type": "command",
            "payload": {"intent": "echo"},
            "context": "tx-time",
            "in_reply_to": None,
        }
        (tmp_path / f"{env_id}.json").write_text(json.dumps(env_data), encoding="utf-8")

    response = client.get("/logs?since=2026-04-04T10:30:00Z&until=2026-04-04T11:30:00Z")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["logs"][0]["id"] == "env-mid"


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


def test_create_approval_request_endpoint(monkeypatch):
    monkeypatch.setattr("ai_agent_hub.api_server.save_envelope", lambda env: None)
    monkeypatch.setattr(
        "ai_agent_hub.api_server._find_reply_envelope",
        lambda envelope_id: {
            "inReplyTo": envelope_id,
            "payload": {
                "approval_id": "ap-1",
                "description": "経費申請",
                "approver": "https://company.local/@manager",
                "status": "pending",
            },
        },
    )

    response = client.post(
        "/approvals/request",
        json={
            "description": "経費申請",
            "approver": "https://company.local/@manager",
            "callback": {"intent": "echo"},
            "thread_id": "thread-1",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["description"] == "経費申請"
    assert body["approval_id"] == "ap-1"


def test_get_reply_returns_pending_when_reply_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "REPLIES_DIR", tmp_path)

    response = client.get("/envelopes/missing-id/reply?timeout_sec=0")

    assert response.status_code == 200
    assert response.json() == {"status": "pending"}
