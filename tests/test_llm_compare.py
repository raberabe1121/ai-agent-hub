from __future__ import annotations

from ai_agent_hub import Envelope
import ai_agent_hub.agent_worker as agent_worker


def _make_env(payload):
    return Envelope.new(
        envelope_type="command",
        sender="https://example.com/@alice",
        recipient="https://agent.local/@worker",
        payload=payload,
    )


def test_llm_compare_returns_divergence_score(monkeypatch):
    def fake_query(provider, text, payload):
        return {
            "provider": f"{provider}/model",
            "result": f"{provider} answer about {text}",
        }

    monkeypatch.setattr(agent_worker, "_query_llm_provider", fake_query)
    monkeypatch.setattr(
        agent_worker, "_embed_texts", lambda texts: [[1.0, 0.0], [0.0, 1.0]]
    )

    reply = agent_worker._handle_envelope(
        _make_env(
            {
                "intent": "llm-compare",
                "query": "compare this",
                "providers": ["ollama", "openai"],
            }
        )
    )

    assert reply is not None
    assert reply.payload["query"] == "compare this"
    assert reply.payload["answers"] == [
        {"provider": "ollama/model", "answer": "ollama answer about compare this"},
        {"provider": "openai/model", "answer": "openai answer about compare this"},
    ]
    assert reply.payload["divergence_score"] == 0.5
    assert reply.payload["bias_alert"] is False


def test_llm_compare_accepts_text_payload(monkeypatch):
    def fake_query(provider, text, payload):
        return {"provider": f"{provider}/model", "result": f"answer for {text}"}

    monkeypatch.setattr(agent_worker, "_query_llm_provider", fake_query)
    monkeypatch.setattr(
        agent_worker, "_embed_texts", lambda texts: [[1.0, 0.0], [1.0, 0.0]]
    )

    reply = agent_worker._handle_envelope(
        _make_env(
            {
                "intent": "llm-compare",
                "text": "hub cli query",
                "providers": ["ollama", "openai"],
            }
        )
    )

    assert reply is not None
    assert reply.payload["query"] == "hub cli query"
    assert reply.payload["answers"] == [
        {"provider": "ollama/model", "answer": "answer for hub cli query"},
        {"provider": "openai/model", "answer": "answer for hub cli query"},
    ]


def test_llm_compare_sets_bias_alert_when_low_divergence(monkeypatch):
    def fake_query(provider, text, payload):
        return {"provider": f"{provider}/model", "result": "same answer"}

    monkeypatch.setattr(agent_worker, "_query_llm_provider", fake_query)
    monkeypatch.setattr(
        agent_worker, "_embed_texts", lambda texts: [[1.0, 0.0], [1.0, 0.0]]
    )

    reply = agent_worker._handle_envelope(
        _make_env(
            {
                "intent": "llm-compare",
                "query": "same?",
                "providers": ["ollama", "openai"],
            }
        )
    )

    assert reply is not None
    assert reply.payload["divergence_score"] == 0.0
    assert reply.payload["bias_alert"] is True
    assert reply.payload["bias_reason"] == "全プロバイダーが類似した回答を返しています"


def test_llm_compare_auto_rag_index(monkeypatch):
    calls = []

    class FakeStore:
        def add_document(self, *, content, source, metadata, embedding_text):
            calls.append(
                {
                    "content": content,
                    "source": source,
                    "metadata": metadata,
                    "embedding_text": embedding_text,
                }
            )
            return 123

    def fake_query(provider, text, payload):
        return {"provider": f"{provider}/model", "result": f"{provider} result"}

    monkeypatch.setattr(agent_worker, "_query_llm_provider", fake_query)
    monkeypatch.setattr(
        agent_worker, "_embed_texts", lambda texts: [[1.0, 0.0], [1.0, 0.0]]
    )
    monkeypatch.setattr(agent_worker, "_get_rag_store", lambda: FakeStore())

    reply = agent_worker._handle_envelope(
        _make_env(
            {
                "intent": "llm-compare",
                "query": "persist me",
                "providers": ["ollama", "openai"],
                "auto_rag_index": True,
            }
        )
    )

    assert reply is not None
    assert reply.payload["rag_doc_id"] == 123
    assert reply.payload["rag_source"].startswith("llm-compare/")
    assert calls[0]["source"] == reply.payload["rag_source"]
    assert calls[0]["metadata"] == {
        "query": "persist me",
        "providers": ["ollama/model", "openai/model"],
    }
    assert calls[0]["embedding_text"] == "ollama result\n\nopenai result"
