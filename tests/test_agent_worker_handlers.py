"""Unit tests for agent_worker intent handlers (ping/echo/summarize)."""

from __future__ import annotations

import json

import pytest

from ai_agent_hub import Envelope
from ai_agent_hub.agent_worker import _handle_envelope


BASE_SENDER = "https://example.com/@alice"
BASE_RECIPIENT = "https://agent.local/@worker"


def _make_env(payload) -> Envelope:
    return Envelope.new(
        envelope_type="command",
        sender=BASE_SENDER,
        recipient=BASE_RECIPIENT,
        payload=payload,
    )


def _assert_reply_envelope(reply: Envelope, original: Envelope) -> None:
    assert reply.envelope_type == "reply"
    assert reply.sender == original.recipient
    assert reply.recipient == original.sender
    assert reply.in_reply_to == original.id


@pytest.mark.parametrize(
    ("payload", "expected_payload"),
    [
        ({"intent": "ping"}, {"pong": True}),
        ({"intent": "echo", "text": "hello"}, {"echo": "hello"}),
    ],
)
def test_handlers_happy_path_return_expected_response(payload, expected_payload) -> None:
    env = _make_env(payload)

    reply = _handle_envelope(env)

    assert reply is not None
    _assert_reply_envelope(reply, env)
    assert reply.payload == expected_payload


def test_summarize_happy_path_returns_shortened_text() -> None:
    env = _make_env({"intent": "summarize", "text": "word " * 80})

    reply = _handle_envelope(env)

    assert reply is not None
    _assert_reply_envelope(reply, env)
    summary = reply.payload.get("summary")
    assert isinstance(summary, str)
    assert len(summary) <= 100
    assert summary.endswith("…")


@pytest.mark.parametrize(
    "payload",
    [
        {"intent": "ping", "anything": []},
        {"intent": "echo"},
        {"intent": "echo", "text": 123},
        {"intent": "summarize"},
        {"intent": "summarize", "text": None},
    ],
)
def test_handlers_do_not_crash_on_empty_or_unexpected_payload_types(payload) -> None:
    env = _make_env(payload)

    reply = _handle_envelope(env)

    assert reply is not None
    _assert_reply_envelope(reply, env)

    intent = payload["intent"]
    if intent == "ping":
        assert reply.payload == {"pong": True}
    elif intent == "echo":
        expected_echo = payload.get("text") if isinstance(payload.get("text"), str) else json.dumps(payload, ensure_ascii=False)
        assert reply.payload == {"echo": expected_echo}
    else:
        expected_summary = json.dumps(payload, ensure_ascii=False)
        assert reply.payload == {"summary": expected_summary}
