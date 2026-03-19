"""E2E test for SMTP -> systemd LMTP -> queue persistence and worker pickup."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import smtplib
import uuid
from pathlib import Path

import pytest

pytest.importorskip("pytest_asyncio")

SYSTEM_QUEUE_DIR = Path("/opt/ai-agent-hub/queue")
SYSTEM_PROCESSED_DIR = Path("/opt/ai-agent-hub/processed")

# Configure storage paths before importing project modules used by the test.
os.environ["AI_AGENT_HUB_QUEUE_DIR"] = str(SYSTEM_QUEUE_DIR)
os.environ["AI_AGENT_HUB_PROCESSED_DIR"] = str(SYSTEM_PROCESSED_DIR)

from ai_agent_hub import Envelope
from ai_agent_hub.smtp_sender import send_envelope_via_smtp


async def wait_for_matching_envelope(
    directory: Path,
    context: str,
    timeout_sec: float = 10.0,
) -> Path | None:
    """Wait for an envelope JSON file whose context matches the provided token."""

    deadline = asyncio.get_running_loop().time() + timeout_sec
    while asyncio.get_running_loop().time() < deadline:
        for candidate in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("context") == context:
                return candidate
        await asyncio.sleep(0.2)
    return None


@pytest.mark.asyncio
async def test_smtp_to_systemd_lmtp_persists_and_worker_reads_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E2E: SMTP submit -> systemd LMTP receive -> queue JSON -> worker processes it."""

    if not SYSTEM_QUEUE_DIR.exists():
        pytest.skip(f"System queue directory not available: {SYSTEM_QUEUE_DIR}")
    if not SYSTEM_PROCESSED_DIR.exists():
        pytest.skip(f"System processed directory not available: {SYSTEM_PROCESSED_DIR}")

    try:
        with smtplib.SMTP("localhost", 25, timeout=2) as smtp:
            smtp.noop()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"SMTP localhost:25 not available: {exc}")

    context_token = f"pytest-e2e-{uuid.uuid4()}"
    env = Envelope.new(
        envelope_type="command",
        sender="https://example.com/@alice",
        recipient="https://agent.local/@worker",
        payload={"intent": "ping"},
        context=context_token,
    )

    await asyncio.to_thread(send_envelope_via_smtp, env)

    queued_file = await wait_for_matching_envelope(SYSTEM_QUEUE_DIR, context_token)
    assert queued_file is not None, "No matching envelope JSON persisted to system queue"

    monkeypatch.setenv("AI_AGENT_HUB_QUEUE_DIR", str(SYSTEM_QUEUE_DIR))
    monkeypatch.setenv("AI_AGENT_HUB_PROCESSED_DIR", str(SYSTEM_PROCESSED_DIR))

    import ai_agent_hub.agent_worker as agent_worker

    agent_worker = importlib.reload(agent_worker)
    processed = await asyncio.to_thread(agent_worker.process_next_envelope)
    assert processed is True, "process_next_envelope() did not process a queued envelope"

