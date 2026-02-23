"""Agent worker that processes queued envelopes and dispatches intents."""
from __future__ import annotations

import json
import os
import textwrap
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ai_agent_hub import Envelope
from ai_agent_hub.lmtp_handler import get_queue_dir
from ai_agent_hub.smtp_sender import send_envelope_via_smtp

PROCESSED_DIR = Path(
    os.environ.get("AI_AGENT_HUB_PROCESSED_DIR")
    or os.environ.get("AGENT_HUB_PROCESSED_DIR")
    or "./processed"
)


INTENT_HANDLERS: Dict[str, Callable[[Envelope], Optional[Any]]] = {}


def intent_handler(name: str) -> Callable[[Callable[[Envelope], Optional[Any]]], Callable[[Envelope], Optional[Any]]]:
    """Decorator to register an intent handler."""

    def decorator(func: Callable[[Envelope], Optional[Any]]) -> Callable[[Envelope], Optional[Any]]:
        INTENT_HANDLERS[name] = func
        return func

    return decorator


def _find_oldest_queue_file() -> Optional[Path]:
    queue_dir = get_queue_dir()
    if not queue_dir.exists():
        return None

    files = [p for p in queue_dir.iterdir() if p.is_file()]
    if not files:
        return None

    return sorted(files, key=lambda p: p.stat().st_mtime)[0]


def _load_envelope(file_path: Path) -> Envelope:
    raw = file_path.read_text(encoding="utf-8")
    return Envelope.from_json(raw)


def _extract_intent(env: Envelope) -> Optional[str]:
    payload = env.payload
    if isinstance(payload, dict):
        intent = payload.get("intent")
        if isinstance(intent, str):
            return intent
    return None


@intent_handler("ping")
def _handle_ping(_: Envelope) -> dict:
    return {"pong": True}


@intent_handler("echo")
def _handle_echo(env: Envelope) -> dict:
    text = ""
    if isinstance(env.payload, dict):
        text_val = env.payload.get("text")
        if isinstance(text_val, str):
            text = text_val
        else:
            text = json.dumps(env.payload, ensure_ascii=False)
    else:
        text = str(env.payload)
    return {"echo": text}


@intent_handler("help")
@intent_handler("list-intents")
def _handle_help(_: Envelope) -> dict:
    return {"intents": sorted(INTENT_HANDLERS.keys())}


@intent_handler("summarize")
def _handle_summarize(env: Envelope) -> dict:
    text = ""
    if isinstance(env.payload, dict):
        payload_text = env.payload.get("text")
        if isinstance(payload_text, str):
            text = payload_text
        else:
            text = json.dumps(env.payload, ensure_ascii=False)
    else:
        text = str(env.payload)

    summary = textwrap.shorten(text, width=100, placeholder="…")
    return {"summary": summary}


def _build_reply(env: Envelope, result_payload: Any) -> Envelope:
    return Envelope.new(
        envelope_type="reply",
        sender=env.recipient,
        recipient=env.sender,
        payload=result_payload,
        context=env.context,
        in_reply_to=env.id,
    )


def _handle_envelope(env: Envelope) -> Optional[Envelope]:
    intent_name = _extract_intent(env)
    if not intent_name:
        print("No intent found; skipping envelope", env.id)
        return None

    handler = INTENT_HANDLERS.get(intent_name)

    if handler:
        print(
            f"[agent_worker] intent={intent_name} from={env.sender} → handler={handler.__name__}"
        )
        try:
            reply_payload = handler(env)
        except Exception as exc:  # pragma: no cover - safeguard
            print(f"Handler error for intent '{intent_name}': {exc}")
            reply_payload = {"error": str(exc)}
    else:
        print(f"[agent_worker] intent={intent_name} from={env.sender} → handler=UNKNOWN")
        reply_payload = {"error": "unknown intent"}

    if reply_payload is None:
        return None

    return _build_reply(env, reply_payload)


def process_next_envelope() -> bool:
    """Process the oldest envelope in the queue if present."""

    file_path = _find_oldest_queue_file()
    if not file_path:
        return False

    env = _load_envelope(file_path)
    reply = _handle_envelope(env)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    destination = PROCESSED_DIR / file_path.name
    file_path.rename(destination)

    if reply:
        send_envelope_via_smtp(reply)
    return True


def main(poll_interval: float = 1.0) -> None:
    """Continuously watch the queue directory and process envelopes."""

    while True:
        processed = process_next_envelope()
        if not processed:
            time.sleep(poll_interval)


if __name__ == "__main__":
    main()
