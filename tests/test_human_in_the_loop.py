from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

import pytest

from ai_agent_hub import Envelope
import ai_agent_hub.agent_worker as agent_worker
from ai_agent_hub.human_in_the_loop import ApprovalRequest, ApprovalStore


BASE_SENDER = "https://example.com/@alice"
BASE_RECIPIENT = "https://agent.local/@worker"
APPROVER = "https://company.local/@manager"


@pytest.fixture()
def approval_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "approvals.db"
    monkeypatch.setenv("AI_AGENT_HUB_APPROVAL_DB", str(db_path))
    return db_path


def _make_env(
    payload: dict,
    *,
    sender: str = BASE_SENDER,
    recipient: str = BASE_RECIPIENT,
    context: str = "thread-1",
) -> Envelope:
    return Envelope.new(
        envelope_type="command",
        sender=sender,
        recipient=recipient,
        payload=payload,
        context=context,
    )


def test_request_approval_creates_approval_request(approval_db: Path) -> None:
    env = _make_env(
        {
            "intent": "request-approval",
            "description": "経費申請 ¥150,000の承認をお願いします",
            "approver": APPROVER,
            "callback_payload": {
                "intent": "execute-approved-task",
                "task": "経費を承認済みとしてDBに記録する",
            },
        }
    )

    reply = agent_worker._handle_envelope(env)

    assert reply is not None
    assert reply.payload == {
        "status": "pending",
        "approval_id": env.id,
        "message": "承認待ちです",
    }

    stored = ApprovalStore(str(approval_db)).get(env.id)
    assert stored is not None
    assert stored.envelope_id == env.id
    assert stored.thread_id == "thread-1"
    assert stored.description == "経費申請 ¥150,000の承認をお願いします"
    assert stored.requester == BASE_SENDER
    assert stored.approver == APPROVER
    assert stored.status == "pending"
    assert stored.decided_at is None
    assert stored.callback_payload == {
        "intent": "execute-approved-task",
        "task": "経費を承認済みとしてDBに記録する",
    }


def test_request_approval_accepts_text_json_payload_fallback(approval_db: Path) -> None:
    env = _make_env(
        {
            "intent": "request-approval",
            "text": (
                '{"description":"JSON経由の承認",'
                '"approver":"https://company.local/@manager",'
                '"callback_payload":{"intent":"execute-approved-task"}}'
            ),
        }
    )

    reply = agent_worker._handle_envelope(env)

    assert reply is not None
    assert reply.payload == {
        "status": "pending",
        "approval_id": env.id,
        "message": "承認待ちです",
    }

    stored = ApprovalStore(str(approval_db)).get(env.id)
    assert stored is not None
    assert stored.description == "JSON経由の承認"


def test_approve_sends_follow_up_envelope(approval_db: Path, sent_envelopes: list[Envelope]) -> None:
    request_env = _make_env(
        {
            "intent": "request-approval",
            "description": "経費申請 ¥150,000の承認をお願いします",
            "approver": APPROVER,
            "callback_payload": {
                "intent": "execute-approved-task",
                "task": "経費を承認済みとしてDBに記録する",
            },
        },
        context="expense-thread",
    )
    agent_worker._handle_envelope(request_env)

    approve_env = _make_env(
        {"intent": "approve", "approval_id": request_env.id},
        sender=APPROVER,
    )

    reply = agent_worker._handle_envelope(approve_env)

    assert reply is not None
    assert reply.payload == {"status": "approved", "message": "承認しました"}

    stored = ApprovalStore(str(approval_db)).get(request_env.id)
    assert stored is not None
    assert stored.status == "approved"
    assert stored.decided_at is not None

    assert len(sent_envelopes) == 1
    callback = sent_envelopes[0]
    assert callback.envelope_type == "command"
    assert callback.sender == APPROVER
    assert callback.recipient == BASE_SENDER
    assert callback.context == "expense-thread"
    assert callback.in_reply_to == request_env.id
    assert callback.payload == {
        "intent": "execute-approved-task",
        "task": "経費を承認済みとしてDBに記録する",
    }


def test_reject_updates_status(approval_db: Path) -> None:
    request_env = _make_env(
        {
            "intent": "request-approval",
            "description": "経費申請 ¥150,000の承認をお願いします",
            "approver": APPROVER,
            "callback_payload": {"intent": "execute-approved-task"},
        }
    )
    agent_worker._handle_envelope(request_env)

    reject_env = _make_env(
        {"intent": "reject", "approval_id": request_env.id, "reason": "予算超過"},
        sender=APPROVER,
    )

    reply = agent_worker._handle_envelope(reject_env)

    assert reply is not None
    assert reply.payload == {"status": "rejected", "reason": "予算超過"}

    stored = ApprovalStore(str(approval_db)).get(request_env.id)
    assert stored is not None
    assert stored.status == "rejected"
    assert stored.decided_at is not None


def test_list_pending_approvals_returns_only_pending_entries(
    approval_db: Path,
    sent_envelopes: list[Envelope],
) -> None:
    pending_env = _make_env(
        {
            "intent": "request-approval",
            "description": "未処理の承認",
            "approver": APPROVER,
            "callback_payload": {"intent": "execute-approved-task"},
        },
        context="thread-pending",
    )
    approved_env = _make_env(
        {
            "intent": "request-approval",
            "description": "あとで承認済みにする依頼",
            "approver": APPROVER,
            "callback_payload": {"intent": "execute-approved-task"},
        },
        context="thread-approved",
    )
    agent_worker._handle_envelope(pending_env)
    agent_worker._handle_envelope(approved_env)
    agent_worker._handle_envelope(_make_env({"intent": "approve", "approval_id": approved_env.id}, sender=APPROVER))

    reply = agent_worker._handle_envelope(_make_env({"intent": "list-pending-approvals"}))

    assert reply is not None
    pending = reply.payload.get("pending")
    assert isinstance(pending, list)
    assert len(pending) == 1
    assert pending[0]["envelope_id"] == pending_env.id
    assert pending[0]["status"] == "pending"
    assert pending[0]["description"] == "未処理の承認"


def test_approve_unknown_approval_id_returns_error(approval_db: Path, sent_envelopes: list[Envelope]) -> None:
    reply = agent_worker._handle_envelope(
        _make_env({"intent": "approve", "approval_id": "missing-approval"}, sender=APPROVER)
    )

    assert reply is not None
    assert reply.payload == {"error": "approval request not found: missing-approval"}
    assert sent_envelopes == []


def test_approval_store_reads_env_path_at_method_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ApprovalStore()
    db_path = tmp_path / "shared-approvals.db"
    monkeypatch.setenv("AI_AGENT_HUB_APPROVAL_DB", str(db_path))

    request = ApprovalRequest(
        envelope_id="approval-1",
        thread_id="thread-dynamic",
        description="env path dynamic check",
        requester=BASE_SENDER,
        approver=APPROVER,
        status="pending",
        created_at=datetime.now(timezone.utc),
        decided_at=None,
        callback_payload={"intent": "echo"},
    )

    store.create(request)
    stored = store.get("approval-1")

    assert stored is not None
    assert db_path.exists()
