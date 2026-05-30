"""FastAPI server for AI Agent Hub Docker quickstart."""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ai_agent_hub import Envelope
from ai_agent_hub.human_in_the_loop import ApprovalRequest, ApprovalStore
from ai_agent_hub.smtp_sender import send_envelope_via_smtp
from ai_agent_hub.rag import RAGStore
import ai_agent_hub.agent_worker as agent_worker


QUEUE_DIR = Path(os.environ.get("AI_AGENT_HUB_QUEUE_DIR", "./queue"))
PROCESSED_DIR = Path(os.environ.get("AI_AGENT_HUB_PROCESSED_DIR", "./processed"))
REPLIES_DIR = Path(os.environ.get("AI_AGENT_HUB_REPLIES_DIR", "./replies"))
RAG_STORE: RAGStore | None = None


def _get_rag_store() -> RAGStore:
    global RAG_STORE
    if RAG_STORE is None:
        RAG_STORE = RAGStore(os.environ.get("AI_AGENT_HUB_DB_PATH", "agent_hub.db"))
    return RAG_STORE


class EnvelopeRequest(BaseModel):
    intent: str
    text: str | None = None
    answers: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None
    sender: str | None = None
    model: str | None = None
    description: str | None = None
    approver: str | None = None
    callback_payload: dict[str, Any] | None = None
    thread_id: str | None = None


class RejectRequest(BaseModel):
    reason: str


class ApprovalCreateRequest(BaseModel):
    description: str
    approver: str
    callback: dict[str, Any]
    thread_id: str | None = None


class RagIndexRequest(BaseModel):
    text: str
    source: str | None = None
    metadata: dict[str, Any] | None = None
    embedding_text: str | None = None


class RagQueryRequest(BaseModel):
    query: str
    limit: int = 5
    use_llm: bool = True
    max_distance: float | None = None


app = FastAPI(title="AI Agent Hub API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _iter_json_files(directory: Path):
    if not directory.exists():
        return []
    return sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime)


def _load_envelope_from_file(file_path: Path) -> dict[str, Any]:
    return json.loads(file_path.read_text(encoding="utf-8"))


def _find_envelope(envelope_id: str) -> dict[str, Any] | None:
    for file_path in _iter_json_files(PROCESSED_DIR):
        data = _load_envelope_from_file(file_path)
        if data.get("id") == envelope_id:
            return data
    return None


def _find_reply_envelope(envelope_id: str) -> dict[str, Any] | None:
    reply_path = REPLIES_DIR / f"{envelope_id}.json"
    if not reply_path.exists():
        return None
    return _load_envelope_from_file(reply_path)


def _envelope_to_log_item(data: dict[str, Any]) -> dict[str, Any]:
    payload = data.get("payload")
    intent = payload.get("intent") if isinstance(payload, dict) else None
    return {
        "id": data.get("id"),
        "time": data.get("time", data.get("created_at")),
        "intent": intent,
        "from": data.get("from", data.get("sender")),
        "to": data.get("to", data.get("recipient")),
        "type": data.get("type", data.get("envelope_type")),
        "payload": payload,
        "in_reply_to": data.get("inReplyTo", data.get("in_reply_to")),
        "context": data.get("context"),
    }


def _parse_iso8601(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_log_time(data: dict[str, Any]) -> datetime | None:
    raw = data.get("time", data.get("created_at"))
    if not isinstance(raw, str):
        return None
    try:
        return _parse_iso8601(raw)
    except ValueError:
        return None


def _approval_store() -> ApprovalStore:
    approval_db = os.environ.get("AI_AGENT_HUB_APPROVAL_DB")
    return ApprovalStore(db_path=approval_db)


@app.post("/envelopes")
def create_envelope(request: EnvelopeRequest) -> dict[str, str]:
    print("=== INCOMING REQUEST ===")
    if hasattr(request, "model_dump_json"):
        print(request.model_dump_json())
    else:  # pragma: no cover - Pydantic v1 fallback
        print(request.json())
    payload: dict[str, Any] = dict(request.payload) if isinstance(request.payload, dict) else {}
    payload["intent"] = request.intent
    if request.text is not None:
        payload["text"] = request.text
    if request.answers is not None:
        payload["answers"] = request.answers
    if request.model is not None:
        payload["model"] = request.model
    if request.description is not None:
        payload["description"] = request.description
    if request.approver is not None:
        payload["approver"] = request.approver
    if request.callback_payload is not None:
        payload["callback_payload"] = request.callback_payload
    if request.intent == "request-approval" and isinstance(request.text, str):
        try:
            text_payload = json.loads(request.text)
        except json.JSONDecodeError:
            text_payload = None
        if isinstance(text_payload, dict):
            if "description" not in payload and isinstance(text_payload.get("description"), str):
                payload["description"] = text_payload["description"]
            if "approver" not in payload and isinstance(text_payload.get("approver"), str):
                payload["approver"] = text_payload["approver"]
            if "callback_payload" not in payload and isinstance(text_payload.get("callback_payload"), dict):
                payload["callback_payload"] = text_payload["callback_payload"]

    sender = request.sender or "https://user.local/@me"
    env = Envelope.new(
        envelope_type="email",
        sender=sender,
        recipient="https://ai-agent.local/@worker",
        payload=payload,
        context=request.thread_id,
    )
    send_envelope_via_smtp(env)
    return {"envelope_id": env.id, "status": "queued"}


@app.get("/envelopes/{envelope_id}")
def get_envelope(envelope_id: str) -> dict[str, Any]:
    envelope = _find_envelope(envelope_id)
    if envelope is None:
        raise HTTPException(status_code=404, detail="envelope not found")
    return envelope


@app.get("/envelopes/{envelope_id}/reply")
def get_reply(envelope_id: str, timeout_sec: int = 30) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        reply = _find_reply_envelope(envelope_id)
        if reply is not None:
            return reply
        time.sleep(1)
    return {"status": "pending"}


@app.get("/logs")
def get_logs(
    thread_id: str | None = None,
    limit: int = 20,
    offset: int = 0,
    intent: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    since_dt: datetime | None = None
    until_dt: datetime | None = None
    if since is not None:
        try:
            since_dt = _parse_iso8601(since)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid since format; expected ISO 8601") from exc
    if until is not None:
        try:
            until_dt = _parse_iso8601(until)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid until format; expected ISO 8601") from exc

    files = _iter_json_files(PROCESSED_DIR)
    sorted_files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    logs: list[dict[str, Any]] = []
    for file_path in sorted_files:
        data = _load_envelope_from_file(file_path)
        if thread_id is not None and data.get("context") != thread_id:
            continue
        log_time = _parse_log_time(data)
        if since_dt is not None and (log_time is None or log_time < since_dt):
            continue
        if until_dt is not None and (log_time is None or log_time > until_dt):
            continue
        payload = data.get("payload")
        payload_intent = payload.get("intent") if isinstance(payload, dict) else None
        if intent is not None and payload_intent != intent:
            continue
        logs.append(_envelope_to_log_item(data))

    return {"logs": logs[offset : offset + limit], "total": len(logs)}


@app.get("/approvals/pending")
def list_pending_approvals() -> list[dict[str, Any]]:
    store = _approval_store()
    return [item.to_dict() for item in store.list_pending()]


@app.post("/approvals/request")
def create_approval_request(request: ApprovalCreateRequest) -> dict[str, Any]:
    env = Envelope.new(
        envelope_type="email",
        sender="https://user.local/@me",
        recipient="https://ai-agent.local/@worker",
        payload={
            "intent": "request-approval",
            "description": request.description,
            "approver": request.approver,
            "callback_payload": request.callback,
        },
        context=request.thread_id,
    )
    send_envelope_via_smtp(env)

    deadline = time.time() + 30
    while time.time() < deadline:
        reply = _find_reply_envelope(env.id)
        if isinstance(reply, dict):
            payload = reply.get("payload")
            if isinstance(payload, dict):
                approval_id = payload.get("approval_id")
                if isinstance(approval_id, str) and approval_id:
                    return {
                        "approval_id": approval_id,
                        "description": str(payload.get("description", request.description)),
                        "approver": str(payload.get("approver", request.approver)),
                        "status": str(payload.get("status", "pending")),
                    }
        time.sleep(1)

    raise HTTPException(status_code=404, detail="approval reply not found within timeout")


@app.post("/approvals/{approval_id}/approve")
def approve_request(approval_id: str) -> dict[str, Any]:
    store = _approval_store()
    try:
        return store.approve(approval_id).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/approvals/{approval_id}/reject")
def reject_request(approval_id: str, request: RejectRequest) -> dict[str, Any]:
    store = _approval_store()
    try:
        return store.reject(approval_id, request.reason).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc




@app.post("/rag/index")
def rag_index(request: RagIndexRequest) -> dict[str, Any]:
    doc_id = _get_rag_store().add_document(content=request.text, source=request.source, metadata=request.metadata, embedding_text=request.embedding_text)
    return {"status": "indexed", "doc_id": doc_id, "source": request.source}


@app.post("/rag/query")
def rag_query(request: RagQueryRequest) -> dict[str, Any]:
    docs = _get_rag_store().search(
        query=request.query,
        limit=request.limit,
        max_distance=request.max_distance,
    )
    sources = [
        {
            "id": item["id"],
            "content": item["content"],
            "source": item.get("source"),
            "distance": item.get("distance"),
        }
        for item in docs
    ]

    response: dict[str, Any] = {"sources": sources, "query": request.query}
    if request.use_llm:
        if not docs:
            return {"answer": "関連するドキュメントが見つかりませんでした", "sources": [], "query": request.query}
        context = "\n".join(f"{idx + 1}. {item['content']}" for idx, item in enumerate(docs))
        prompt = (
            "以下のコンテキストを参照して質問に答えてください。\n\n"
            f"コンテキスト:\n{context}\n\n"
            f"質問: {request.query}"
        )
        llm_env = Envelope.new(
            envelope_type="command",
            sender="https://user.local/@me",
            recipient="https://ai-agent.local/@worker",
            payload={"intent": "llm-query", "text": prompt},
        )
        llm_result = agent_worker._handle_llm_query(llm_env)
        if isinstance(llm_result, dict) and isinstance(llm_result.get("result"), str):
            response["answer"] = llm_result["result"]
        else:
            response["answer"] = ""
    return response

@app.get("/intents")
def list_intents() -> list[dict[str, str]]:
    from ai_agent_hub.agent_worker import INTENT_HANDLERS

    return [{"name": name} for name in INTENT_HANDLERS.keys()]


@app.get("/health")
def health() -> dict[str, Any]:
    services = {
        "queue_dir": str(QUEUE_DIR),
        "processed_dir": str(PROCESSED_DIR),
        "queue_dir_exists": QUEUE_DIR.exists(),
        "processed_dir_exists": PROCESSED_DIR.exists(),
        "smtp_host": os.environ.get("SMTP_HOST", "localhost"),
        "smtp_port": int(os.environ.get("SMTP_PORT", "25")),
    }
    return {
        "status": "ok",
        "lmtp": True,
        "api": True,
        "queue_dir": services["queue_dir_exists"],
        "services": services,
    }


def main() -> None:
    import uvicorn

    uvicorn.run(
        "ai_agent_hub.api_server:app",
        host=os.environ.get("API_HOST", "0.0.0.0"),
        port=int(os.environ.get("API_PORT", "8080")),
        reload=False,
    )


if __name__ == "__main__":
    main()
