from __future__ import annotations

from ai_agent_hub import Envelope
import ai_agent_hub.agent_worker as agent_worker


class FakeRAGStore:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    def add_document(self, content: str, source: str = None, metadata: dict | None = None) -> int:
        doc_id = len(self.docs) + 1
        self.docs.append({"id": doc_id, "content": content, "source": source, "metadata": metadata})
        return doc_id

    def search(self, query: str, limit: int = 5) -> list[dict]:
        ranked = sorted(
            self.docs,
            key=lambda d: 0 if query.lower() in d["content"].lower() else 1,
        )
        return [
            {**doc, "distance": float(idx)} for idx, doc in enumerate(ranked[:limit])
        ]


def _make_env(payload):
    return Envelope.new(
        envelope_type="command",
        sender="https://example.com/@alice",
        recipient="https://agent.local/@worker",
        payload=payload,
    )


def test_add_and_search() -> None:
    store = FakeRAGStore()
    store.add_document("python asyncio tutorial", source="doc1")
    results = store.search("asyncio")
    assert results
    assert results[0]["content"] == "python asyncio tutorial"


def test_search_relevance() -> None:
    store = FakeRAGStore()
    store.add_document("cat care guide")
    store.add_document("database tuning tips")
    results = store.search("cat")
    assert results[0]["content"] == "cat care guide"


def test_rag_index_intent(monkeypatch) -> None:
    fake_store = FakeRAGStore()
    monkeypatch.setattr(agent_worker, "_get_rag_store", lambda: fake_store)

    reply = agent_worker._handle_envelope(_make_env({"intent": "rag-index", "text": "hello", "source": "memo"}))

    assert reply is not None
    assert reply.payload["status"] == "indexed"
    assert reply.payload["doc_id"] == 1


def test_rag_query_intent(monkeypatch) -> None:
    fake_store = FakeRAGStore()
    fake_store.add_document("RAG retrieves relevant context", source="kb")
    monkeypatch.setattr(agent_worker, "_get_rag_store", lambda: fake_store)
    monkeypatch.setattr(agent_worker, "_handle_llm_query", lambda env: {"result": "mocked answer"})

    reply = agent_worker._handle_envelope(_make_env({"intent": "rag-query", "query": "relevant", "use_llm": True}))

    assert reply is not None
    assert reply.payload["answer"] == "mocked answer"
    assert reply.payload["sources"]


def test_rag_query_no_llm(monkeypatch) -> None:
    fake_store = FakeRAGStore()
    fake_store.add_document("search-only mode doc", source="kb")
    monkeypatch.setattr(agent_worker, "_get_rag_store", lambda: fake_store)

    reply = agent_worker._handle_envelope(_make_env({"intent": "rag-query", "query": "search", "use_llm": False}))

    assert reply is not None
    assert "answer" not in reply.payload
    assert len(reply.payload["sources"]) == 1
