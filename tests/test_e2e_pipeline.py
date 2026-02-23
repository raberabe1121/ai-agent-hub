"""End-to-end pipeline test for AI Agent Hub.

This test exercises the full flow:
1. Send an envelope via SMTP to Postfix.
2. Postfix forwards to the LMTP server, which stores queue JSON.
3. Agent worker processes queue items and replies.
4. Replies are round-tripped back through LMTP and processed.

The test relies on a locally running Postfix instance listening on
localhost:25 and the asyncio LMTP server being able to start.
"""
from __future__ import annotations

import json
import os
import socket
import smtplib
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
import unittest

import pytest

from ai_agent_hub import Envelope
from ai_agent_hub.agent_worker import PROCESSED_DIR, process_next_envelope
from ai_agent_hub.lmtp_handler import get_queue_dir
from ai_agent_hub.smtp_sender import send_envelope_via_smtp


def clean_dirs() -> None:
    """Remove all files inside queue and processed directories."""

    for directory in (get_queue_dir(), PROCESSED_DIR):
        if directory.exists():
            for path in directory.iterdir():
                if path.is_file():
                    path.unlink()
        else:
            directory.mkdir(parents=True, exist_ok=True)


def run_lmtp_server_background() -> subprocess.Popen:
    """Start the asyncio LMTP server as a background subprocess."""

    env = {
        **os.environ,
        "AI_AGENT_HUB_QUEUE_DIR": str(get_queue_dir()),
        "PYTHONPATH": str(Path(__file__).parent.parent),
    }

    process = subprocess.Popen(
        [sys.executable, "-m", "ai_agent_hub.lmtp_server"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    for _ in range(50):
        time.sleep(0.1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            result = sock.connect_ex(("127.0.0.1", 8024))
        if result == 0:
            break
    else:
        debug_log = Path("/tmp/lmtp_debug.log")
        if debug_log.exists():
            print("=== LMTP DEBUG LOG ===")
            print(debug_log.read_text(encoding="utf-8"))
        pytest.fail("LMTP server failed to start")

    return process


def run_agent_worker_once() -> bool:
    """Process a single envelope from the queue using the agent worker."""

    return process_next_envelope()


def wait_for_file_in_queue(pattern: str, timeout_sec: float = 5.0) -> Optional[Path]:
    """Wait for a file matching pattern to appear in the queue directory."""

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        matches = list(get_queue_dir().glob(pattern))
        if matches:
            return matches[0]
        time.sleep(0.1)
    return None


def send_test_envelope_via_smtp(env: Envelope) -> None:
    """Send the provided envelope via SMTP to localhost:25."""

    send_envelope_via_smtp(env)


def assert_response_payload(json_data: dict) -> None:
    """Assert that the response payload equates to a pong reply."""

    payload = json_data.get("payload")
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            decoded = None
        else:
            payload = decoded.get("payload") if isinstance(decoded, dict) else payload
    if isinstance(payload, dict):
        payload = payload.get("payload")
    assert payload == "pong", f"Expected 'pong' payload, got {payload!r}"


class TestE2EPipeline(unittest.TestCase):
    def setUp(self) -> None:
        clean_dirs()

    def _require_environment(self) -> None:
        try:
            with smtplib.SMTP("localhost", 25, timeout=1) as smtp:
                smtp.noop()
        except Exception as exc:  # pragma: no cover - environment dependent
            self.skipTest(f"SMTP localhost:25 not available: {exc}")

    def test_ping_pong_round_trip(self) -> None:
        self._require_environment()

        env = Envelope.new(
            envelope_type="command",
            sender="https://example.com/@alice",
            recipient="https://agent.local/@worker",
            payload={"intent": "ping"},
        )

        process = run_lmtp_server_background()
        try:
            # Give LMTP server time to start listening.
            time.sleep(0.5)

            send_test_envelope_via_smtp(env)

            incoming = wait_for_file_in_queue("*.json", timeout_sec=5)
            self.assertIsNotNone(incoming, "No incoming envelope persisted to queue")

            # Process the command envelope and emit a response.
            processed = run_agent_worker_once()
            self.assertTrue(processed, "Agent worker did not process the incoming envelope")

            reply_file = wait_for_file_in_queue("*.json", timeout_sec=5)
            self.assertIsNotNone(reply_file, "Reply envelope was not written to queue")
            assert reply_file is not None  # for type narrowing

            # Process the reply envelope (pong) and move to processed directory.
            processed_reply = run_agent_worker_once()
            self.assertTrue(processed_reply, "Agent worker did not process the reply envelope")

            final_location = PROCESSED_DIR / reply_file.name
            self.assertTrue(final_location.exists(), "Processed reply file missing")

            reply_json = json.loads(final_location.read_text(encoding="utf-8"))
            assert_response_payload(reply_json)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                process.kill()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
