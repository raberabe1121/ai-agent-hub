"""Helper utilities for LMTP email → Envelope conversion.

This module previously hosted the aiosmtpd-based LMTP handler. It now only
exposes helper functions reused by the asyncio LMTP server implementation.
"""

from __future__ import annotations

import json
import os
import re
from datetime import timezone
from pathlib import Path

from ai_agent_hub import Envelope


# ActivityPub Agent ID pattern: https://domain/@name
_AGENT_ID_PATTERN = re.compile(r"(https?://[a-zA-Z0-9.\-]+/@[a-zA-Z0-9_.\-]+)")

__all__ = [
    "get_queue_dir",
    "extract_sender",
    "extract_recipient",
    "extract_body",
    "save_envelope",
]


def get_queue_dir() -> Path:
    """Return the queue directory, evaluating environment variables at call time."""

    return Path(
        os.environ.get("AI_AGENT_HUB_QUEUE_DIR")
        or os.environ.get("AGENT_HUB_QUEUE_DIR")
        or "./queue"
    )


def extract_sender(msg) -> str:
    """Extract the sender ActivityPub agent ID from the ``From`` header."""

    return _extract_agent_id(msg.get("From"))


def extract_recipient(msg) -> str:
    """Extract the recipient ActivityPub agent ID from the ``To`` header."""

    return _extract_agent_id(msg.get("To"))


def _extract_agent_id(raw_header: str | None) -> str:
    """Extract an ActivityPub-style agent ID from an email header."""

    if not raw_header:
        return "https://unknown/@unknown"

    sanitized = re.sub(r"[<>]", " ", raw_header)
    sanitized = re.sub(
        r"https?\s*:\s*//",
        lambda m: m.group(0).replace(" ", ""),
        sanitized,
    )
    match = _AGENT_ID_PATTERN.search(sanitized)
    if match:
        return match.group(1).rstrip("/")

    return "https://unknown/@unknown"


def extract_body(msg) -> str:
    """Extract body and auto-parse JSON if applicable."""

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                raw = part.get_payload(decode=True).decode(errors="replace")
                return _maybe_json(raw)
        return ""

    payload = msg.get_payload(decode=True)
    text = (
        payload.decode(errors="replace")
        if isinstance(payload, (bytes, bytearray))
        else str(payload)
    )
    return _maybe_json(text)


def _maybe_json(text: str):
    try:
        return json.loads(text)
    except Exception:
        return text


def save_envelope(env: Envelope):
    """Persist an envelope to the queue directory using an OS-safe filename."""

    queue_dir = get_queue_dir()
    queue_dir.mkdir(parents=True, exist_ok=True)
    timestamp = env.created_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fname = f"{timestamp}_{env.id}.json"
    fpath = queue_dir / fname
    with fpath.open("w", encoding="utf-8") as f:
        f.write(env.to_json(indent=2))
    print(f"Saved envelope → {fpath}")
