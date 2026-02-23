import pytest

from ai_agent_hub import Envelope
from ai_agent_hub.agent_worker import INTENT_HANDLERS


def _make_env(payload) -> Envelope:
    return Envelope.new(
        envelope_type="command",
        sender="https://example.com/@alice",
        recipient="https://agent.local/@worker",
        payload=payload,
    )


def _assert_reply_structure(reply: Envelope, original: Envelope):
    assert reply.envelope_type == "reply"
    assert reply.sender == original.recipient
    assert reply.recipient == original.sender
    assert reply.in_reply_to == original.id


@pytest.mark.parametrize("intent_payload, expected_payload", [
    ({"intent": "ping"}, {"pong": True}),
    ({"intent": "echo", "text": "hello"}, {"echo": "hello"}),
])
def test_basic_intents(intent_payload, expected_payload, enqueue, process_once, sent_envelopes, queue_dirs):
    queue_dir, processed_dir = queue_dirs
    env = _make_env(intent_payload)
    enqueue(env)

    processed = process_once()
    assert processed is True

    assert not any(queue_dir.iterdir())
    processed_files = list(processed_dir.iterdir())
    assert len(processed_files) == 1

    assert len(sent_envelopes) == 1
    reply = sent_envelopes[0]
    _assert_reply_structure(reply, env)
    assert reply.payload == expected_payload


def test_help_intent_lists_handlers(enqueue, process_once, sent_envelopes, queue_dirs):
    queue_dir, processed_dir = queue_dirs
    env = _make_env({"intent": "help"})
    enqueue(env)

    assert process_once() is True
    assert not any(queue_dir.iterdir())
    assert len(list(processed_dir.iterdir())) == 1

    assert len(sent_envelopes) == 1
    reply = sent_envelopes[0]
    _assert_reply_structure(reply, env)

    intents = reply.payload.get("intents")
    assert isinstance(intents, list)
    expected = set(INTENT_HANDLERS.keys())
    assert set(intents) >= expected


def test_list_intents_alias(enqueue, process_once, sent_envelopes, queue_dirs):
    queue_dir, processed_dir = queue_dirs
    env = _make_env({"intent": "list-intents"})
    enqueue(env)

    assert process_once() is True
    assert not any(queue_dir.iterdir())
    assert len(list(processed_dir.iterdir())) == 1

    assert len(sent_envelopes) == 1
    reply = sent_envelopes[0]
    _assert_reply_structure(reply, env)

    intents = reply.payload.get("intents")
    assert isinstance(intents, list)
    assert set(intents) >= set(INTENT_HANDLERS.keys())


def test_summarize_intent(enqueue, process_once, sent_envelopes, queue_dirs):
    queue_dir, processed_dir = queue_dirs
    text = "a" * 200
    env = _make_env({"intent": "summarize", "text": text})
    enqueue(env)

    assert process_once() is True
    assert not any(queue_dir.iterdir())
    assert len(list(processed_dir.iterdir())) == 1

    assert len(sent_envelopes) == 1
    reply = sent_envelopes[0]
    _assert_reply_structure(reply, env)

    summary = reply.payload.get("summary")
    assert isinstance(summary, str)
    assert len(summary) < len(text)


def test_unknown_intent(enqueue, process_once, sent_envelopes, queue_dirs):
    queue_dir, processed_dir = queue_dirs
    env = _make_env({"intent": "does-not-exist"})
    enqueue(env)

    assert process_once() is True
    assert not any(queue_dir.iterdir())
    assert len(list(processed_dir.iterdir())) == 1

    assert len(sent_envelopes) == 1
    reply = sent_envelopes[0]
    _assert_reply_structure(reply, env)
    assert reply.payload == {"error": "unknown intent"}
