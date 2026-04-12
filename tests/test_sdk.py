from __future__ import annotations

import pytest

import ai_agent_hub.sdk as sdk_module
from ai_agent_hub.sdk import (
    AgentHub,
    AgentHubConnectionError,
    AgentHubTimeoutError,
    ApprovalEntry,
    LogEntry,
)


class DummyResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class DummyClient:
    def __init__(self, responses=None, error=None):
        self.responses = responses or []
        self.error = error
        self.calls = []

    def request(self, method, path, timeout=None, **kwargs):
        self.calls.append({"method": method, "path": path, "timeout": timeout, **kwargs})
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise AssertionError("No dummy response available")
        return self.responses.pop(0)


def test_send_returns_envelope_id_and_reply_payload(monkeypatch):
    client = DummyClient(
        responses=[
            DummyResponse(200, {"envelope_id": "env-1", "status": "queued"}),
            DummyResponse(200, {"id": "env-r", "payload": {"pong": True}}),
        ]
    )
    hub = AgentHub(base_url="http://localhost:8080")
    monkeypatch.setattr(hub, "_client", client)

    result = hub.send(intent="ping")

    assert result.envelope_id == "env-1"
    assert result.payload == {"pong": True}
    assert result.status == "ok"


def test_send_wait_false_does_not_fetch_reply(monkeypatch):
    client = DummyClient(responses=[DummyResponse(200, {"envelope_id": "env-2", "status": "queued"})])
    hub = AgentHub(base_url="http://localhost:8080")
    monkeypatch.setattr(hub, "_client", client)

    result = hub.send(intent="echo", text="hello", wait=False)

    assert result.envelope_id == "env-2"
    assert result.payload is None
    assert result.status == "queued"
    assert len(client.calls) == 1
    assert client.calls[0]["path"] == "/envelopes"


def test_logs_returns_log_entries(monkeypatch):
    client = DummyClient(
        responses=[
            DummyResponse(
                200,
                {
                    "logs": [
                        {
                            "id": "log-1",
                            "time": "2026-04-04T13:58:33Z",
                            "intent": "ping",
                            "from": "https://user.local/@me",
                            "to": "https://agent.local/@worker",
                            "payload": {"pong": True},
                            "in_reply_to": None,
                            "context": "tx-1",
                        }
                    ]
                },
            )
        ]
    )
    hub = AgentHub(base_url="http://localhost:8080")
    monkeypatch.setattr(hub, "_client", client)

    entries = hub.logs(limit=10)

    assert len(entries) == 1
    assert isinstance(entries[0], LogEntry)
    assert entries[0].id == "log-1"
    assert entries[0].sender == "https://user.local/@me"


def test_pending_approvals_returns_entries(monkeypatch):
    client = DummyClient(
        responses=[
            DummyResponse(
                200,
                [
                    {
                        "approval_id": "ap-1",
                        "description": "経費申請",
                        "approver": "https://company.local/@manager",
                        "status": "pending",
                    }
                ],
            )
        ]
    )
    hub = AgentHub(base_url="http://localhost:8080")
    monkeypatch.setattr(hub, "_client", client)

    entries = hub.pending_approvals()

    assert len(entries) == 1
    assert isinstance(entries[0], ApprovalEntry)
    assert entries[0].approval_id == "ap-1"


def test_approve_returns_dict(monkeypatch):
    client = DummyClient(responses=[DummyResponse(200, {"approval_id": "ap-1", "status": "approved"})])
    hub = AgentHub(base_url="http://localhost:8080")
    monkeypatch.setattr(hub, "_client", client)

    result = hub.approve("ap-1")

    assert result["status"] == "approved"


def test_health_returns_dict(monkeypatch):
    client = DummyClient(responses=[DummyResponse(200, {"status": "ok"})])
    hub = AgentHub(base_url="http://localhost:8080")
    monkeypatch.setattr(hub, "_client", client)

    result = hub.health()

    assert result["status"] == "ok"


def test_connection_error_raises_custom_exception(monkeypatch):
    client = DummyClient(error=sdk_module.httpx.ConnectError("connect failed"))
    hub = AgentHub(base_url="http://localhost:8080")
    monkeypatch.setattr(hub, "_client", client)

    with pytest.raises(AgentHubConnectionError):
        hub.health()


def test_timeout_error_raises_custom_exception(monkeypatch):
    client = DummyClient(error=sdk_module.httpx.ReadTimeout("timed out"))
    hub = AgentHub(base_url="http://localhost:8080")
    monkeypatch.setattr(hub, "_client", client)

    with pytest.raises(AgentHubTimeoutError):
        hub.health()


def test_base_url_from_environment(monkeypatch):
    monkeypatch.setenv("AI_AGENT_HUB_URL", "http://192.168.1.1:8080")

    hub = AgentHub()

    assert hub.base_url == "http://192.168.1.1:8080"
