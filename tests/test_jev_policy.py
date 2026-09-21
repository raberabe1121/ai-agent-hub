from __future__ import annotations

from types import SimpleNamespace

from ai_agent_hub import Envelope
from ai_agent_hub.policy import JevPolicyEngine, PolicyEngine, get_policy_engine


def _env(intent: str, text: str = "") -> Envelope:
    return Envelope.new(
        envelope_type="email",
        sender="https://user.local/@me",
        recipient="https://ai-agent.local/@worker",
        payload={"intent": intent, "text": text},
    )


def _client_with(*, allow: float, needs_human: float, risk: float):
    class _Client:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def system_one(self, **kwargs):
            return SimpleNamespace(
                nouls={
                    "allow": SimpleNamespace(noul=allow),
                    "needs_human": SimpleNamespace(noul=needs_human),
                },
                scores={"risk": SimpleNamespace(score=risk)},
            )

    return _Client


def test_jev_policy_blocks_dangerous_command(monkeypatch) -> None:
    monkeypatch.setenv("JEV_API_KEY", "test-key")
    monkeypatch.setattr(
        "ai_agent_hub.policy.TypeSafeClient",
        _client_with(allow=0.2, needs_human=0.1, risk=3.0),
    )
    result = JevPolicyEngine().evaluate(_env("cli-skill", "rm -rf /"))

    assert result.allowed is False
    assert result.action == "block"
    assert "許可確率20%" in result.reason


def test_jev_policy_allows_safe_command(monkeypatch) -> None:
    monkeypatch.setenv("JEV_API_KEY", "test-key")
    monkeypatch.setenv("POLICY_BACKEND", "jev")
    monkeypatch.setattr(
        "ai_agent_hub.policy.TypeSafeClient",
        _client_with(allow=0.9, needs_human=0.1, risk=0.0),
    )
    engine = get_policy_engine()
    result = engine.evaluate(_env("echo", "hello"))

    assert isinstance(engine, JevPolicyEngine)
    assert result.allowed is True
    assert result.action == "pass"


def test_jev_policy_falls_back_when_no_api_key(monkeypatch) -> None:
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.setenv("POLICY_BACKEND", "jev")
    engine = get_policy_engine()
    result = JevPolicyEngine().evaluate(_env("echo", "secret_key=abc"))

    assert isinstance(engine, PolicyEngine)
    assert result.allowed is False
    assert result.action == "block"
