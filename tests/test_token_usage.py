from __future__ import annotations

import pytest

from ai_agent_hub.token_usage import TokenUsageStore


def test_record_and_summary(tmp_path) -> None:
    store = TokenUsageStore(str(tmp_path / "usage.db"))

    store.record("env-1", "llm-query", "gemma3:4b", "ollama", 10, 5)
    store.record("env-2", "llm-compare", "gemma3:4b", "ollama", 7, 3)

    assert store.summary() == {
        "prompt_tokens": 17,
        "completion_tokens": 8,
        "total_tokens": 25,
        "count": 2,
    }


def test_summary_by_intent(tmp_path) -> None:
    store = TokenUsageStore(str(tmp_path / "usage.db"))

    store.record("env-1", "llm-query", "gemma3:4b", "ollama", 10, 5)
    store.record("env-2", "llm-query", "gemma3:4b", "ollama", 20, 10)
    store.record("env-3", "llm-compare", "gemma3:4b", "ollama", 7, 3)

    assert store.summary_by_intent() == [
        {
            "intent": "llm-query",
            "prompt_tokens": 30,
            "completion_tokens": 15,
            "total_tokens": 45,
            "count": 2,
        },
        {
            "intent": "llm-compare",
            "prompt_tokens": 7,
            "completion_tokens": 3,
            "total_tokens": 10,
            "count": 1,
        },
    ]


def test_token_usage_api_endpoint(tmp_path, monkeypatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from ai_agent_hub.api_server import app

    db_path = tmp_path / "usage.db"
    monkeypatch.setenv("AI_AGENT_HUB_SQLITE_PATH", str(db_path))
    store = TokenUsageStore(str(db_path))
    store.record("env-1", "llm-query", "gemma3:4b", "ollama", 12, 6)

    client = TestClient(app)
    response = client.get("/token-usage")

    assert response.status_code == 200
    assert response.json() == {
        "summary": {
            "prompt_tokens": 12,
            "completion_tokens": 6,
            "total_tokens": 18,
            "count": 1,
        },
        "by_intent": [
            {
                "intent": "llm-query",
                "prompt_tokens": 12,
                "completion_tokens": 6,
                "total_tokens": 18,
                "count": 1,
            }
        ],
    }

    by_intent_response = client.get("/token-usage/by-intent")
    assert by_intent_response.status_code == 200
    assert by_intent_response.json()[0]["intent"] == "llm-query"
