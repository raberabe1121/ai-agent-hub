from __future__ import annotations

from ai_agent_hub import Envelope
from ai_agent_hub.agent_worker import INTENT_HANDLERS, _handle_envelope
from ai_agent_hub.entropy_monitor import DIVERGENT_CONTEXTS, EntropyMonitor


def _make_env(payload) -> Envelope:
    return Envelope.new(
        envelope_type="command",
        sender="https://example.com/@alice",
        recipient="https://agent.local/@worker",
        payload=payload,
    )


def test_repeated_messages_have_low_entropy() -> None:
    monitor = EntropyMonitor()
    thread_id = "thread-repeat"

    for _ in range(10):
        monitor.add_message(thread_id, "same idea same idea")

    assert monitor.get_entropy(thread_id) < 0.3


def test_diverse_messages_have_high_entropy() -> None:
    monitor = EntropyMonitor()
    thread_id = "thread-diverse"
    for message in [
        "red fox jumps swiftly over winter snow",
        "quantum circuits optimize strange computation paths",
        "marine biologists catalog luminous reef species",
        "ancient pottery reveals trade across deserts",
    ]:
        monitor.add_message(thread_id, message)

    assert monitor.get_entropy(thread_id) > 0.9


def test_is_low_entropy_respects_threshold() -> None:
    monitor = EntropyMonitor()
    repeated_thread = "thread-threshold-low"
    diverse_thread = "thread-threshold-high"

    for _ in range(5):
        monitor.add_message(repeated_thread, "echo echo echo")

    for message in [
        "orbits drift through silent vacuum",
        "gardens bloom beneath mountain rain",
        "algorithms compare uncertain futures",
    ]:
        monitor.add_message(diverse_thread, message)

    assert monitor.is_low_entropy(repeated_thread, threshold=0.1) is True
    assert monitor.is_low_entropy(diverse_thread, threshold=0.5) is False


def test_inject_divergent_context_returns_expected_string() -> None:
    monitor = EntropyMonitor()

    injected = monitor.inject_divergent_context("thread-any")

    assert injected in DIVERGENT_CONTEXTS


def test_check_and_inject_returns_context_when_entropy_is_low() -> None:
    monitor = EntropyMonitor()
    thread_id = "thread-check"
    for _ in range(4):
        monitor.add_message(thread_id, "uniform uniform uniform")

    injected = monitor.check_and_inject(thread_id, threshold=0.3)

    assert injected in DIVERGENT_CONTEXTS


def test_check_and_inject_returns_none_when_entropy_is_high() -> None:
    monitor = EntropyMonitor()
    thread_id = "thread-no-inject"
    for message in [
        "oranges rotate around curious galaxies",
        "libraries preserve forgotten coastal maps",
        "engineers prototype resilient transit systems",
    ]:
        monitor.add_message(thread_id, message)

    assert monitor.check_and_inject(thread_id, threshold=0.3) is None


def test_entropy_check_intent_is_registered() -> None:
    assert "entropy-check" in INTENT_HANDLERS


def test_entropy_check_intent_marks_repeated_messages_as_low_entropy() -> None:
    env = _make_env(
        {
            "intent": "entropy-check",
            "thread_id": "thread-intent",
            "messages": ["same message"] * 10,
        }
    )

    reply = _handle_envelope(env)

    assert reply is not None
    assert reply.payload["is_low"] is True
    assert reply.payload["entropy"] < 0.3
    assert reply.payload["injected_context"] in DIVERGENT_CONTEXTS
