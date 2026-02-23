"""Async E2E test for SMTP -> LMTP -> queue persistence."""

from __future__ import annotations

import asyncio
import smtplib
from pathlib import Path

import pytest

pytest_asyncio = pytest.importorskip("pytest_asyncio")

from ai_agent_hub import Envelope
from ai_agent_hub.lmtp_server import LMTPServer
from ai_agent_hub.smtp_sender import send_envelope_via_smtp


async def wait_for_queue_file(queue_dir: Path, timeout_sec: float = 5.0) -> Path | None:
    """Poll queue directory until at least one JSON file appears."""

    deadline = asyncio.get_running_loop().time() + timeout_sec
    while asyncio.get_running_loop().time() < deadline:
        matches = sorted(queue_dir.glob("*.json"))
        if matches:
            return matches[0]
        await asyncio.sleep(0.1)
    return None


@pytest.mark.asyncio
async def test_smtp_to_lmtp_persists_envelope_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E2E: SMTP submit -> LMTP receive -> queue JSON persisted."""

    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AI_AGENT_HUB_QUEUE_DIR", str(queue_dir))

    try:
        with smtplib.SMTP("localhost", 25, timeout=1) as smtp:
            smtp.noop()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"SMTP localhost:25 not available: {exc}")

    server = LMTPServer(port=8024)
    await server.start()

    try:
        env = Envelope.new(
            envelope_type="command",
            sender="https://example.com/@alice",
            recipient="https://agent.local/@worker",
            payload={"intent": "ping"},
        )

        await asyncio.to_thread(send_envelope_via_smtp, env)

        queued_file = await wait_for_queue_file(queue_dir, timeout_sec=5)
        assert queued_file is not None, "No envelope JSON persisted to queue"
    finally:
        await server.stop()
