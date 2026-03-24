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


def test_llm_query_uses_ollama_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, *, headers: dict[str, str], json: dict[str, object], timeout: float):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {"message": {"content": "mocked ollama answer"}}

        return FakeResponse()

    fake_httpx_module = SimpleNamespace(post=fake_post)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test-key")
    monkeypatch.setattr(
        agent_worker.importlib,
        "import_module",
        lambda name: fake_httpx_module if name == "httpx" else None,
    )

    reply = agent_worker._handle_envelope(
        _make_env(
            {
                "intent": "llm-query",
                "text": "hello from ollama",
                "model": "gemma3:12b",
            }
        )
    )

    assert reply is not None
    assert reply.payload == {"result": "mocked ollama answer"}
    assert captured == {
        "url": "https://api.ollama.com/api/chat",
        "headers": {"Authorization": "Bearer ollama-test-key"},
        "json": {
            "model": "gemma3:12b",
            "messages": [{"role": "user", "content": "hello from ollama"}],
            "stream": False,
        },
        "timeout": 30.0,
    }


def test_llm_query_uses_ollama_when_provider_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, *, headers: dict[str, str], json: dict[str, object], timeout: float):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {"message": {"content": "explicit ollama answer"}}

        return FakeResponse()

    fake_httpx_module = SimpleNamespace(post=fake_post)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-provider-key")
    monkeypatch.setattr(
        agent_worker.importlib,
        "import_module",
        lambda name: fake_httpx_module if name == "httpx" else None,
    )

    reply = agent_worker._handle_envelope(
        _make_env({"intent": "llm-query", "text": "hello provider"})
    )

    assert reply is not None
    assert reply.payload == {"result": "explicit ollama answer"}
    assert captured["url"] == "https://api.ollama.com/api/chat"
    assert captured["headers"] == {"Authorization": "Bearer ollama-provider-key"}
    assert captured["json"] == {
        "model": "gemma3:4b",
        "messages": [{"role": "user", "content": "hello provider"}],
        "stream": False,
    }


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
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        agent_worker.importlib,
        "import_module",
        lambda name: fake_openai_module if name == "openai" else None,
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
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    reply = agent_worker._handle_envelope(
        _make_env({"intent": "llm-query", "text": "hello"})
    )

    assert reply is not None
    assert reply.payload == {"error": "OPENAI_API_KEY is not set"}


def test_llm_query_returns_error_without_ollama_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)

    reply = agent_worker._handle_envelope(
        _make_env({"intent": "llm-query", "text": "hello"})
    )

    assert reply is not None
    assert reply.payload == {"error": "OLLAMA_API_KEY is not set"}


def test_llm_query_uses_payload_api_key_when_env_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, *, headers: dict[str, str], json: dict[str, object], timeout: float):
        captured["headers"] = headers

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {"message": {"content": "payload key answer"}}

        return FakeResponse()

    fake_httpx_module = SimpleNamespace(post=fake_post)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setattr(
        agent_worker.importlib,
        "import_module",
        lambda name: fake_httpx_module if name == "httpx" else None,
    )

    reply = agent_worker._handle_envelope(
        _make_env({"intent": "llm-query", "text": "hello", "api_key": "payload-key"})
    )

    assert reply is not None
    assert reply.payload == {"result": "payload key answer"}
    assert captured["headers"] == {"Authorization": "Bearer payload-key"}


def test_llm_query_returns_error_when_text_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")

    reply = agent_worker._handle_envelope(_make_env({"intent": "llm-query"}))

    assert reply is not None
    assert reply.payload == {"error": "payload.text is required"}


def test_llm_query_returns_error_when_openai_package_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def _raise_import_error(name: str):
        if name == "openai":
            raise ImportError("missing openai")
        return None

    monkeypatch.setattr(agent_worker.importlib, "import_module", _raise_import_error)

    reply = agent_worker._handle_envelope(
        _make_env({"intent": "llm-query", "text": "hello"})
    )

    assert reply is not None
    assert reply.payload == {"error": "openai package not installed"}


def test_llm_query_returns_ollama_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeHTTPStatusError(Exception):
        pass

    def fake_post(url: str, *, headers: dict[str, str], json: dict[str, object], timeout: float):
        raise FakeHTTPStatusError("Client error '401 Unauthorized' for url")

    fake_httpx_module = SimpleNamespace(post=fake_post)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_API_KEY", "bad-key")
    monkeypatch.setattr(
        agent_worker.importlib,
        "import_module",
        lambda name: fake_httpx_module if name == "httpx" else None,
    )

    reply = agent_worker._handle_envelope(
        _make_env({"intent": "llm-query", "text": "hello"})
    )

    assert reply is not None
    assert "401 Unauthorized" in reply.payload["error"]
