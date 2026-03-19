"""End-to-end pipeline test using the running systemd LMTP server."""
from __future__ import annotations

# 環境変数をimportより先に設定する（これが重要）
import os
os.environ["AI_AGENT_HUB_QUEUE_DIR"] = "/opt/ai-agent-hub/queue"
os.environ["AI_AGENT_HUB_PROCESSED_DIR"] = "/opt/ai-agent-hub/processed"

import json
import socket
import smtplib
import time
from pathlib import Path
from typing import Optional
import unittest

import pytest

from ai_agent_hub import Envelope
from ai_agent_hub.agent_worker import process_next_envelope
from ai_agent_hub.smtp_sender import send_envelope_via_smtp

SYSTEM_QUEUE_DIR = Path(os.environ["AI_AGENT_HUB_QUEUE_DIR"])
PROCESSED_DIR = Path(os.environ["AI_AGENT_HUB_PROCESSED_DIR"])


def clean_dirs() -> None:
    for directory in (SYSTEM_QUEUE_DIR, PROCESSED_DIR):
        if directory.exists():
            for path in directory.iterdir():
                if path.is_file():
                    path.unlink()
        else:
            directory.mkdir(parents=True, exist_ok=True)


def wait_for_new_file(before: set, timeout_sec: float = 5.0) -> Optional[Path]:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        current = set(SYSTEM_QUEUE_DIR.glob("*.json"))
        new_files = current - before
        if new_files:
            return next(iter(new_files))
        time.sleep(0.1)
    return None


class TestE2EPipeline(unittest.TestCase):
    def setUp(self) -> None:
        clean_dirs()

    def _require_environment(self) -> None:
        try:
            with smtplib.SMTP("localhost", 25, timeout=1) as smtp:
                smtp.noop()
        except Exception as exc:
            self.skipTest(f"SMTP localhost:25 not available: {exc}")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(("127.0.0.1", 8024)) != 0:
                self.skipTest("LMTP server not running on :8024")

    def test_ping_pong_round_trip(self) -> None:
        self._require_environment()

        env = Envelope.new(
            envelope_type="command",
            sender="https://example.com/@alice",
            recipient="https://agent.local/@worker",
            payload={"intent": "ping"},
        )

        before_send = set(SYSTEM_QUEUE_DIR.glob("*.json"))
        send_envelope_via_smtp(env)

        incoming = wait_for_new_file(before_send, timeout_sec=5)
        self.assertIsNotNone(incoming, "No incoming envelope persisted to queue")

        before_reply = set(SYSTEM_QUEUE_DIR.glob("*.json"))
        processed = process_next_envelope()
        self.assertTrue(processed, "Agent worker did not process the incoming envelope")

        reply_file = wait_for_new_file(before_reply, timeout_sec=5)
        self.assertIsNotNone(reply_file, "Reply envelope was not written to queue")

        processed_reply = process_next_envelope()
        self.assertTrue(processed_reply, "Agent worker did not process the reply envelope")

        assert reply_file is not None
        final_location = PROCESSED_DIR / reply_file.name
        self.assertTrue(final_location.exists(), "Processed reply file missing")

        reply_json = json.loads(final_location.read_text(encoding="utf-8"))
        payload = reply_json.get("payload")
        self.assertIsNotNone(payload, f"No payload in reply: {reply_json}")
        print(f"\n✅ Reply payload: {payload}")


if __name__ == "__main__":
    unittest.main()
