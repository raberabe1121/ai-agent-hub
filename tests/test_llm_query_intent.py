from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_agent_hub import Envelope
import ai_agent_hub.agent_worker as agent_worker


def _make_env(payload) -> Envelope:
    return Envelope.new(
        envelope_type="command",
        sender="https://example.com/@alice",
        recipient="https://agent.local/@worker",
        payload=payload,
    )


def test_llm_query_uses_openai_client(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    class FakeResponses:
        def create(self, *, model: str, input: str):
            captured["model"] = model
            captured["input"] = input
            return SimpleNamespace(output_text="mocked answer")

    class FakeClient:
        def __init__(self, *, api_key: str) -> None:
            captured["api_key"] = api_key
            self.responses = FakeResponses()

    fake_openai_module = SimpleNamespace(OpenAI=FakeClient)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        agent_worker.importlib,
        "import_module",
        lambda name: fake_openai_module,
    )

    reply = agent_worker._handle_envelope(
        _make_env(
            {
                "intent": "llm-query",
                "text": "hello from test",
                "model": "gpt-4.1-mini",
            }
        )
    )

    assert reply is not None
    assert reply.payload == {"result": "mocked answer"}
    assert captured == {
        "api_key": "test-key",
        "model": "gpt-4.1-mini",
        "input": "hello from test",
    }


def test_llm_query_returns_error_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    reply = agent_worker._handle_envelope(
        _make_env({"intent": "llm-query", "text": "hello"})
    )

    assert reply is not None
    assert reply.payload == {"error": "OPENAI_API_KEY is not set"}


def test_llm_query_returns_error_when_text_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    reply = agent_worker._handle_envelope(_make_env({"intent": "llm-query"}))

    assert reply is not None
    assert reply.payload == {"error": "payload.text is required"}


def test_llm_query_returns_error_when_openai_package_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def _raise_import_error(_: str):
        raise ImportError("missing openai")

    monkeypatch.setattr(agent_worker.importlib, "import_module", _raise_import_error)

    reply = agent_worker._handle_envelope(
        _make_env({"intent": "llm-query", "text": "hello"})
    )

    assert reply is not None
    assert reply.payload == {"error": "openai package not installed"}
