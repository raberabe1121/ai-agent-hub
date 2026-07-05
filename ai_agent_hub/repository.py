"""Storage repositories for LMTP email → Envelope conversion."""

from __future__ import annotations

import json
import os
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from ai_agent_hub import Envelope

PENDING = "pending"
PROCESSED = "processed"
FAILED = "failed"


class EnvelopeRepository(ABC):
    """Abstract storage interface for envelopes."""

    @abstractmethod
    def save(self, env: Envelope) -> None:
        """Persist an envelope as pending."""

    @abstractmethod
    def find_by_id(self, id: str) -> Envelope | None:
        """Return an envelope by id, or None when missing."""

    @abstractmethod
    def list_pending(self) -> list[Envelope]:
        """Return pending envelopes in processing order."""


class FileSystemRepository(EnvelopeRepository):
    """Repository backed by queue JSON files in the filesystem."""

    def __init__(
        self,
        queue_dir: Path,
        processed_dir: Path | None = None,
    ) -> None:
        self.queue_dir = queue_dir
        self.processed_dir = processed_dir or get_processed_dir()

    def save(self, env: Envelope) -> None:
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        timestamp = env.created_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        fpath = self.queue_dir / f"{timestamp}_{env.id}.json"
        fpath.write_text(env.to_json(indent=2), encoding="utf-8")
        print(f"Saved envelope → {fpath}")

    def find_by_id(self, id: str) -> Envelope | None:
        file_path = self.find_file_by_id(id)
        if file_path is None:
            return None
        return Envelope.from_json(file_path.read_text(encoding="utf-8"))

    def list_pending(self) -> list[Envelope]:
        if not self.queue_dir.exists():
            return []

        envelopes: list[Envelope] = []
        for file_path in sorted(self.queue_dir.glob("*.json"), key=lambda p: p.stat().st_mtime):
            try:
                envelopes.append(Envelope.from_json(file_path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return envelopes

    def find_file_by_id(self, id: str) -> Path | None:
        for directory in self._status_directories():
            if not directory.exists():
                continue

            candidates = sorted(directory.glob(f"*_{id}.json"))
            if candidates:
                return candidates[0]

            for file_path in directory.glob("*.json"):
                try:
                    env = Envelope.from_json(file_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if env.id == id:
                    return file_path
        return None

    def _status_directories(self) -> tuple[Path, ...]:
        return (self.queue_dir, self.processed_dir)


class SQLiteRepository(EnvelopeRepository):
    """Repository backed by SQLite."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or Path("./agent_hub.db")
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS envelopes (
                    id TEXT PRIMARY KEY,
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    envelope_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    context TEXT,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'processed', 'failed'))
                )
                """
            )
            conn.commit()

    def save(self, env: Envelope) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO envelopes
                (id, sender, recipient, envelope_type, payload, context, created_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    env.id,
                    env.sender,
                    env.recipient,
                    env.envelope_type,
                    json.dumps(env.payload, ensure_ascii=False),
                    json.dumps(env.context, ensure_ascii=False)
                    if env.context is not None
                    else None,
                    env.created_at.isoformat(),
                    PENDING,
                ),
            )
            conn.commit()

    def find_by_id(self, id: str) -> Envelope | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, sender, recipient, envelope_type, payload, context, created_at
                FROM envelopes
                WHERE id = ?
                """,
                (id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_envelope(row)

    def list_pending(self) -> list[Envelope]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, sender, recipient, envelope_type, payload, context, created_at
                FROM envelopes
                WHERE status = ?
                ORDER BY created_at ASC
                """,
                (PENDING,),
            ).fetchall()
        return [self._row_to_envelope(row) for row in rows]

    def mark_status(self, id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE envelopes SET status = ? WHERE id = ?", (status, id))
            conn.commit()

    def _row_to_envelope(self, row: sqlite3.Row) -> Envelope:
        payload = json.loads(row["payload"])
        context_raw = row["context"]
        context = json.loads(context_raw) if context_raw else None

        created_at = datetime.fromisoformat(row["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        return Envelope.new(
            envelope_id=row["id"],
            envelope_type=row["envelope_type"],
            sender=row["sender"],
            recipient=row["recipient"],
            payload=payload,
            context=context,
            created_at=created_at,
        )


def get_queue_dir() -> Path:
    """Return the queue directory, evaluating environment variables at call time."""

    return Path(
        os.environ.get("AI_AGENT_HUB_QUEUE_DIR")
        or os.environ.get("AGENT_HUB_QUEUE_DIR")
        or "./queue"
    )


def get_processed_dir() -> Path:
    """Return the processed directory, evaluating environment variables at call time."""

    return Path(
        os.environ.get("AI_AGENT_HUB_PROCESSED_DIR")
        or os.environ.get("AGENT_HUB_PROCESSED_DIR")
        or "./processed"
    )


def get_storage_mode() -> str:
    return (os.environ.get("AI_AGENT_HUB_STORAGE") or "filesystem").strip().lower()


def get_repository() -> EnvelopeRepository:
    mode = get_storage_mode()
    if mode == "sqlite":
        db_path = Path(os.environ.get("AI_AGENT_HUB_SQLITE_PATH") or "./agent_hub.db")
        return SQLiteRepository(db_path)
    return FileSystemRepository(get_queue_dir(), get_processed_dir())


def save_envelope(env: Envelope) -> None:
    """Persist an envelope using the configured repository backend."""

    get_repository().save(env)


__all__ = [
    "EnvelopeRepository",
    "FileSystemRepository",
    "SQLiteRepository",
    "get_queue_dir",
    "get_storage_mode",
    "get_repository",
    "get_processed_dir",
    "save_envelope",
    "PENDING",
    "PROCESSED",
    "FAILED",
]
