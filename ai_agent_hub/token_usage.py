"""Token usage storage backed by the AI Agent Hub SQLite database."""
from __future__ import annotations

import os
import sqlite3
from threading import Lock
from typing import Any


DEFAULT_DB_PATH = "agent_hub.db"


def get_default_db_path() -> str:
    """Return the shared SQLite path used by AI Agent Hub components."""
    return (
        os.environ.get("AI_AGENT_HUB_SQLITE_PATH")
        or os.environ.get("AI_AGENT_HUB_DB_PATH")
        or DEFAULT_DB_PATH
    )


class TokenUsageStore:
    """Persist and aggregate LLM token usage by intent."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or get_default_db_path()
        self._tables_lock = Lock()
        self._tables_initialized = False
        self._ensure_tables()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        if self._tables_initialized:
            return
        with self._tables_lock:
            if self._tables_initialized:
                return
            self._init_tables()
            self._tables_initialized = True

    def _init_tables(self) -> None:
        conn = self._get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS token_usage (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  envelope_id TEXT,
                  intent TEXT,
                  model TEXT,
                  provider TEXT,
                  prompt_tokens INTEGER DEFAULT 0,
                  completion_tokens INTEGER DEFAULT 0,
                  total_tokens INTEGER DEFAULT 0,
                  created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def record(
        self,
        envelope_id: str | None,
        intent: str | None,
        model: str | None,
        provider: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> None:
        prompt = int(prompt_tokens or 0)
        completion = int(completion_tokens or 0)
        total = prompt + completion
        conn = self._get_conn()
        try:
            conn.execute(
                """
                INSERT INTO token_usage (
                  envelope_id, intent, model, provider,
                  prompt_tokens, completion_tokens, total_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (envelope_id, intent, model, provider, prompt, completion, total),
            )
            conn.commit()
        finally:
            conn.close()

    def summary_by_intent(self) -> list[dict[str, Any]]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT intent,
                       SUM(prompt_tokens) as prompt_tokens,
                       SUM(completion_tokens) as completion_tokens,
                       SUM(total_tokens) as total_tokens,
                       COUNT(*) as count
                FROM token_usage
                GROUP BY intent
                ORDER BY total_tokens DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def summary(self) -> dict[str, int]:
        conn = self._get_conn()
        try:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) as completion_tokens,
                       COALESCE(SUM(total_tokens), 0) as total_tokens,
                       COUNT(*) as count
                FROM token_usage
                """
            ).fetchone()
            return dict(row) if row is not None else {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "count": 0}
        finally:
            conn.close()
