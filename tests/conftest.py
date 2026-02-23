from pathlib import Path
from typing import List

import pytest

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_agent_hub import Envelope
import ai_agent_hub.agent_worker as agent_worker
import ai_agent_hub.lmtp_handler as lmtp_handler


@pytest.fixture(autouse=True)
def queue_dirs(tmp_path, monkeypatch):
    """Isolate queue/processed directories per test and patch module globals."""

    queue_dir = tmp_path / "queue"
    processed_dir = tmp_path / "processed"
    queue_dir.mkdir()
    processed_dir.mkdir()

    monkeypatch.setattr(agent_worker, "get_queue_dir", lambda: queue_dir)
    monkeypatch.setattr(agent_worker, "PROCESSED_DIR", processed_dir)
    monkeypatch.setattr(lmtp_handler, "get_queue_dir", lambda: queue_dir)

    return queue_dir, processed_dir


@pytest.fixture()
def sent_envelopes(monkeypatch) -> List[Envelope]:
    """Capture envelopes that would be sent via SMTP."""

    captured: List[Envelope] = []

    def _fake_send(env: Envelope) -> None:
        captured.append(env)

    monkeypatch.setattr(agent_worker, "send_envelope_via_smtp", _fake_send)
    return captured


@pytest.fixture()
def enqueue(queue_dirs):
    queue_dir, _ = queue_dirs

    def _enqueue(env: Envelope) -> Path:
        file_path = queue_dir / f"{env.id}.json"
        file_path.write_text(env.to_json(indent=2), encoding="utf-8")
        return file_path

    return _enqueue


@pytest.fixture()
def process_once():
    return agent_worker.process_next_envelope
