"""Human-in-the-loop approval persistence for AI Agent Hub."""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_APPROVAL_DB = "./approvals.db"


@dataclass
class ApprovalRequest:
    envelope_id: str
    thread_id: str
    description: str
    requester: str
    approver: str
    status: str
    created_at: datetime
    decided_at: datetime | None
    callback_payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "envelope_id": self.envelope_id,
            "thread_id": self.thread_id,
            "description": self.description,
            "requester": self.requester,
            "approver": self.approver,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "callback_payload": self.callback_payload,
        }


class ApprovalStore:
    """SQLite-backed storage for human approval requests."""

    def __init__(self, db_path: str | None = None) -> None:
        configured_path = (
            db_path
            or os.environ.get("AI_AGENT_HUB_APPROVAL_DB")
            or DEFAULT_APPROVAL_DB
        )
        self.db_path = Path(configured_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS approval_requests (
                    envelope_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    requester TEXT NOT NULL,
                    approver TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    callback_payload TEXT NOT NULL
                )
                """
            )

    def create(self, request: ApprovalRequest) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO approval_requests (
                    envelope_id,
                    thread_id,
                    description,
                    requester,
                    approver,
                    status,
                    created_at,
                    decided_at,
                    callback_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.envelope_id,
                    request.thread_id,
                    request.description,
                    request.requester,
                    request.approver,
                    request.status,
                    request.created_at.isoformat(),
                    request.decided_at.isoformat() if request.decided_at else None,
                    json.dumps(request.callback_payload, ensure_ascii=False),
                ),
            )

    def get(self, envelope_id: str) -> ApprovalRequest | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE envelope_id = ?",
                (envelope_id,),
            ).fetchone()
        return self._row_to_request(row) if row else None

    def approve(self, envelope_id: str) -> ApprovalRequest:
        return self._set_status(envelope_id, "approved")

    def reject(self, envelope_id: str, reason: str) -> ApprovalRequest:
        del reason
        return self._set_status(envelope_id, "rejected")

    def list_pending(self) -> list[ApprovalRequest]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM approval_requests WHERE status = 'pending' ORDER BY created_at ASC"
            ).fetchall()
        return [self._row_to_request(row) for row in rows]

    def _set_status(self, envelope_id: str, status: str) -> ApprovalRequest:
        request = self.get(envelope_id)
        if request is None:
            raise ValueError(f"approval request not found: {envelope_id}")

        decided_at = datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute(
                "UPDATE approval_requests SET status = ?, decided_at = ? WHERE envelope_id = ?",
                (status, decided_at.isoformat(), envelope_id),
            )
        updated = self.get(envelope_id)
        if updated is None:
            raise ValueError(f"approval request not found: {envelope_id}")
        return updated

    def _row_to_request(self, row: sqlite3.Row) -> ApprovalRequest:
        return ApprovalRequest(
            envelope_id=row["envelope_id"],
            thread_id=row["thread_id"],
            description=row["description"],
            requester=row["requester"],
            approver=row["approver"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            decided_at=datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None,
            callback_payload=json.loads(row["callback_payload"]),
        )
