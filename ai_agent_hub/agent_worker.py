"""Agent worker that processes queued envelopes and dispatches intents."""
from __future__ import annotations

import json
import importlib
import os
import textwrap
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ai_agent_hub import Envelope
from ai_agent_hub.entropy_monitor import EntropyMonitor
from ai_agent_hub.payment_gateway import PaymentGateway
from ai_agent_hub.lmtp_handler import (
    FAILED,
    PROCESSED,
    FileSystemRepository,
    SQLiteRepository,
    get_queue_dir,
    get_repository,
)
from ai_agent_hub.smtp_sender import send_envelope_via_smtp

PROCESSED_DIR = Path(
    os.environ.get("AI_AGENT_HUB_PROCESSED_DIR")
    or os.environ.get("AGENT_HUB_PROCESSED_DIR")
    or "./processed"
)


INTENT_HANDLERS: Dict[str, Callable[[Envelope], Optional[Any]]] = {}


def _get_entropy_threshold() -> float:
    return float(os.environ.get("AI_AGENT_HUB_ENTROPY_THRESHOLD", "0.3"))


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
        headers = payload.get("headers")
        if isinstance(headers, dict):
            payment_required = headers.get("X-Agent-Payment-Required")
            if str(payment_required).lower() in {"1", "true", "yes"}:
                return "payment"

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


@intent_handler("entropy-check")
def _handle_entropy_check(env: Envelope) -> dict:
    threshold = _get_entropy_threshold()
    thread_id = "default"
    messages: list[str] = []

    if isinstance(env.payload, dict):
        payload_thread_id = env.payload.get("thread_id")
        if isinstance(payload_thread_id, str) and payload_thread_id:
            thread_id = payload_thread_id

        payload_threshold = env.payload.get("threshold")
        if payload_threshold is not None:
            try:
                threshold = float(payload_threshold)
            except (TypeError, ValueError):
                pass

        payload_messages = env.payload.get("messages")
        if isinstance(payload_messages, list):
            messages = [message for message in payload_messages if isinstance(message, str)]

    monitor = EntropyMonitor()
    for message in messages:
        monitor.add_message(thread_id, message)

    entropy = monitor.get_entropy(thread_id)
    is_low = monitor.is_low_entropy(thread_id, threshold=threshold)
    injected_context = monitor.check_and_inject(thread_id, threshold=threshold)
    return {
        "entropy": entropy,
        "is_low": is_low,
        "injected_context": injected_context,
    }


@intent_handler("payment")
def _handle_payment(env: Envelope) -> dict:
    api_key = os.environ.get("CIRCLE_API_KEY")
    if not api_key:
        return {"error": "CIRCLE_API_KEY is not set"}

    gateway = PaymentGateway(api_key=api_key)
    return gateway.execute(env)


@intent_handler("llm-query")
def _handle_llm_query(env: Envelope) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"error": "OPENAI_API_KEY is not set"}

    text = None
    if isinstance(env.payload, dict):
        text = env.payload.get("text")
    if not text:
        return {"error": "payload.text is required"}

    try:
        openai = importlib.import_module("openai")
    except ImportError:
        return {"error": "openai package not installed"}

    model = "gpt-4o-mini"
    if isinstance(env.payload, dict):
        model = env.payload.get("model", model)

    try:
        client = openai.OpenAI(api_key=api_key)
        response = client.responses.create(model=model, input=text)
        return {"result": response.output_text}
    except Exception as exc:
        return {"error": str(exc)}


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


def _mark_processed(env_id: str) -> None:
    repository = get_repository()

    if isinstance(repository, SQLiteRepository):
        repository.mark_status(env_id, PROCESSED)
        return

    if isinstance(repository, FileSystemRepository):
        file_path = repository.find_file_by_id(env_id)
        if file_path is None:
            return
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        destination = PROCESSED_DIR / file_path.name
        file_path.rename(destination)


def _mark_failed(env_id: str) -> None:
    repository = get_repository()
    if isinstance(repository, SQLiteRepository):
        repository.mark_status(env_id, FAILED)


def process_next_envelope() -> bool:
    """Process the oldest envelope in storage if present."""

    repository = get_repository()
    pending = repository.list_pending()
    if not pending:
        return False

    env = pending[0]
    reply = _handle_envelope(env)

    try:
        _mark_processed(env.id)

        if reply:
            send_envelope_via_smtp(reply)
        return True
    except Exception:
        _mark_failed(env.id)
        raise


def main(poll_interval: float = 1.0) -> None:
    """Continuously watch storage and process envelopes."""

    while True:
        processed = process_next_envelope()
        if not processed:
            time.sleep(poll_interval)


if __name__ == "__main__":
    main()
