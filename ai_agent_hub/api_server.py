"""FastAPI server for AI Agent Hub Docker quickstart."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ai_agent_hub import Envelope
from ai_agent_hub.human_in_the_loop import ApprovalStore
from ai_agent_hub.smtp_sender import send_envelope_via_smtp


QUEUE_DIR = Path(os.environ.get("AI_AGENT_HUB_QUEUE_DIR", "./queue"))
PROCESSED_DIR = Path(os.environ.get("AI_AGENT_HUB_PROCESSED_DIR", "./processed"))


class EnvelopeRequest(BaseModel):
    intent: str
    text: str | None = None
    sender: str | None = None
    model: str | None = None


class RejectRequest(BaseModel):
    reason: str


app = FastAPI(title="AI Agent Hub API")


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
    for file_path in _iter_json_files(PROCESSED_DIR):
        data = _load_envelope_from_file(file_path)
        if data.get("inReplyTo") == envelope_id or data.get("in_reply_to") == envelope_id:
            return data
    return None


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


@app.post("/envelopes")
def create_envelope(request: EnvelopeRequest) -> dict[str, str]:
    payload: dict[str, Any] = {"intent": request.intent}
    if request.text is not None:
        payload["text"] = request.text
    if request.model is not None:
        payload["model"] = request.model

    sender = request.sender or "https://user.local/@me"
    env = Envelope.new(
        envelope_type="email",
        sender=sender,
        recipient="https://ai-agent.local/@worker",
        payload=payload,
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
    raise HTTPException(status_code=404, detail="reply not found within timeout")


@app.get("/logs")
def get_logs(
    thread_id: str | None = None,
    limit: int = 20,
    intent: str | None = None,
) -> dict[str, Any]:
    files = _iter_json_files(PROCESSED_DIR)
    sorted_files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    logs: list[dict[str, Any]] = []
    for file_path in sorted_files:
        data = _load_envelope_from_file(file_path)
        if thread_id is not None and data.get("context") != thread_id:
            continue
        payload = data.get("payload")
        payload_intent = payload.get("intent") if isinstance(payload, dict) else None
        if intent is not None and payload_intent != intent:
            continue
        logs.append(_envelope_to_log_item(data))

    return {"logs": logs[:limit], "total": len(logs)}


@app.get("/approvals/pending")
def list_pending_approvals() -> list[dict[str, Any]]:
    store = ApprovalStore()
    return [item.to_dict() for item in store.list_pending()]


@app.post("/approvals/{approval_id}/approve")
def approve_request(approval_id: str) -> dict[str, Any]:
    store = ApprovalStore()
    try:
        return store.approve(approval_id).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/approvals/{approval_id}/reject")
def reject_request(approval_id: str, request: RejectRequest) -> dict[str, Any]:
    store = ApprovalStore()
    try:
        return store.reject(approval_id, request.reason).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "services": {
            "queue_dir": str(QUEUE_DIR),
            "processed_dir": str(PROCESSED_DIR),
            "queue_dir_exists": QUEUE_DIR.exists(),
            "processed_dir_exists": PROCESSED_DIR.exists(),
            "smtp_host": os.environ.get("SMTP_HOST", "localhost"),
            "smtp_port": int(os.environ.get("SMTP_PORT", "25")),
        },
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
