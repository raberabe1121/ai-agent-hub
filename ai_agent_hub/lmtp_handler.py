"""Helper utilities for LMTP email → Envelope conversion."""

from __future__ import annotations

import json
import re

from ai_agent_hub import Envelope
from ai_agent_hub.repository import (
    FAILED,
    PENDING,
    PROCESSED,
    EnvelopeRepository,
    FileSystemRepository,
    SQLiteRepository,
    get_processed_dir,
    get_queue_dir,
    get_repository,
    get_storage_mode,
)


# ActivityPub Agent ID pattern: https://domain/@name
_AGENT_ID_PATTERN = re.compile(r"(https?://[a-zA-Z0-9.\-]+/@[a-zA-Z0-9_.\-]+)")


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


def extract_body(msg):
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
    """Persist an envelope using configured repository backend."""

    repository = get_repository()
    repository.save(env)


__all__ = [
    "EnvelopeRepository",
    "FileSystemRepository",
    "SQLiteRepository",
    "get_queue_dir",
    "get_processed_dir",
    "get_storage_mode",
    "get_repository",
    "extract_sender",
    "extract_recipient",
    "extract_body",
    "save_envelope",
    "PENDING",
    "PROCESSED",
    "FAILED",
]
