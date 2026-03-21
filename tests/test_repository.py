from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_agent_hub import Envelope
from ai_agent_hub.lmtp_handler import save_envelope
from ai_agent_hub.repository import (
    FileSystemRepository,
    SQLiteRepository,
    get_repository,
)


def _make_envelope(*, envelope_id: str, created_at: datetime | None = None) -> Envelope:
    return Envelope.new(
        envelope_id=envelope_id,
        envelope_type="command",
        sender="https://example.com/@sender",
        recipient="https://example.com/@recipient",
        payload={"intent": "ping", "text": envelope_id},
        context="thread-1",
        created_at=created_at or datetime.now(timezone.utc),
    )


def test_filesystem_repository_save_find_by_id_and_list_pending(tmp_path: Path) -> None:
    queue_dir = tmp_path / "queue"
    processed_dir = tmp_path / "processed"
    repository = FileSystemRepository(queue_dir, processed_dir)
    older = _make_envelope(
        envelope_id="older",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    newer = _make_envelope(envelope_id="newer")

    repository.save(older)
    repository.save(newer)

    older_file = repository.find_file_by_id("older")
    newer_file = repository.find_file_by_id("newer")
    assert older_file is not None
    assert newer_file is not None
    older_timestamp = older.created_at.timestamp()
    newer_timestamp = newer.created_at.timestamp()
    older_file.touch()
    newer_file.touch()
    os.utime(older_file, (older_timestamp, older_timestamp))
    os.utime(newer_file, (newer_timestamp, newer_timestamp))

    processed_dir.mkdir(exist_ok=True)
    processed_file = processed_dir / "20240101T000000Z_processed-only.json"
    processed_file.write_text(newer.to_json(indent=2), encoding="utf-8")

    saved_files = sorted(queue_dir.glob("*.json"))
    assert len(saved_files) == 2
    assert repository.find_by_id("older") == older
    assert repository.find_by_id("newer") == newer
    assert repository.find_by_id("missing") is None
    assert [env.id for env in repository.list_pending()] == ["older", "newer"]


def test_sqlite_repository_save_find_by_id_and_list_pending(tmp_path: Path) -> None:
    db_path = tmp_path / "agent_hub.db"
    repository = SQLiteRepository(db_path)
    pending = _make_envelope(
        envelope_id="pending-env",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    processed = _make_envelope(envelope_id="processed-env")

    repository.save(pending)
    repository.save(processed)
    repository.mark_status("processed-env", "processed")

    assert repository.find_by_id("pending-env") == pending
    assert repository.find_by_id("missing") is None
    assert [env.id for env in repository.list_pending()] == ["pending-env"]

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT status FROM envelopes WHERE id = ?",
            ("pending-env",),
        ).fetchone()
    assert row == ("pending",)


def test_get_repository_uses_filesystem_by_default(tmp_path: Path, monkeypatch) -> None:
    queue_dir = tmp_path / "queue"
    processed_dir = tmp_path / "processed"
    monkeypatch.delenv("AI_AGENT_HUB_STORAGE", raising=False)
    monkeypatch.delenv("AI_AGENT_HUB_SQLITE_PATH", raising=False)
    monkeypatch.setenv("AI_AGENT_HUB_QUEUE_DIR", str(queue_dir))
    monkeypatch.setenv("AI_AGENT_HUB_PROCESSED_DIR", str(processed_dir))

    repository = get_repository()

    assert isinstance(repository, FileSystemRepository)
    assert repository.queue_dir == queue_dir
    assert repository.processed_dir == processed_dir


def test_get_repository_uses_sqlite_when_configured(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "custom.db"
    monkeypatch.setenv("AI_AGENT_HUB_STORAGE", "sqlite")
    monkeypatch.setenv("AI_AGENT_HUB_SQLITE_PATH", str(db_path))

    repository = get_repository()

    assert isinstance(repository, SQLiteRepository)
    assert repository.db_path == db_path


def test_save_envelope_switches_repository_from_environment(tmp_path: Path, monkeypatch) -> None:
    queue_dir = tmp_path / "queue"
    db_path = tmp_path / "agent_hub.db"
    filesystem_env = _make_envelope(envelope_id="filesystem-env")
    sqlite_env = _make_envelope(envelope_id="sqlite-env")

    monkeypatch.setenv("AI_AGENT_HUB_QUEUE_DIR", str(queue_dir))
    monkeypatch.setenv("AI_AGENT_HUB_STORAGE", "filesystem")
    save_envelope(filesystem_env)

    saved_files = list(queue_dir.glob("*.json"))
    assert len(saved_files) == 1
    assert FileSystemRepository(queue_dir).find_by_id("filesystem-env") == filesystem_env

    monkeypatch.setenv("AI_AGENT_HUB_STORAGE", "sqlite")
    monkeypatch.setenv("AI_AGENT_HUB_SQLITE_PATH", str(db_path))
    save_envelope(sqlite_env)

    repository = SQLiteRepository(db_path)
    assert repository.find_by_id("sqlite-env") == sqlite_env
    assert [env.id for env in repository.list_pending()] == ["sqlite-env"]
