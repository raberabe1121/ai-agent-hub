from ai_agent_hub import Envelope
from ai_agent_hub.agent_worker import INTENT_HANDLERS, _handle_envelope


def _make_env(payload):
    return Envelope.new(
        envelope_type="command",
        sender="https://example.com/@alice",
        recipient="https://agent.local/@worker",
        payload=payload,
    )


def test_ping_intent_reply():
    env = _make_env({"intent": "ping"})
    reply = _handle_envelope(env)
    assert reply is not None
    assert reply.envelope_type == "reply"
    assert reply.sender == env.recipient
    assert reply.recipient == env.sender
    assert reply.payload == {"pong": True}


def test_unknown_intent_reply_error():
    env = _make_env({"intent": "unknown"})
    reply = _handle_envelope(env)
    assert reply is not None
    assert reply.payload == {"error": "unknown intent"}


def test_echo_intent_roundtrip():
    env = _make_env({"intent": "echo", "text": "hello"})
    reply = _handle_envelope(env)
    assert reply is not None
    assert reply.payload == {"echo": "hello"}


def test_summarize_intent_shortens_text():
    long_text = "word " * 100
    env = _make_env({"intent": "summarize", "text": long_text})
    reply = _handle_envelope(env)
    assert reply is not None
    assert isinstance(reply.payload, dict)
    summary = reply.payload.get("summary", "")
    assert len(summary) <= 100
    assert summary.endswith("…")


def test_help_lists_registered_intents():
    env = _make_env({"intent": "help"})
    reply = _handle_envelope(env)
    assert reply is not None
    intents = reply.payload.get("intents")
    assert isinstance(intents, list)
    assert "ping" in intents
    assert set(intents) >= {"ping", "echo", "help", "list-intents", "summarize"}
