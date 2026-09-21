from __future__ import annotations

from datetime import datetime

from ai_agent_hub import Envelope
from ai_agent_hub.policy import PolicyEngine


def _env(intent: str, text: str = "") -> Envelope:
    return Envelope.new(
        envelope_type="email",
        sender="https://user.local/@me",
        recipient="https://ai-agent.local/@worker",
        payload={"intent": intent, "text": text},
    )


def _policy(tmp_path, content: str) -> PolicyEngine:
    path = tmp_path / "policy.yaml"
    path.write_text(content, encoding="utf-8")
    return PolicyEngine(str(path))


def test_require_approval_intent(tmp_path) -> None:
    engine = _policy(
        tmp_path,
        'rules:\n  - match:\n      intent: "cli-skill"\n    action: require_approval\n    reason: "CLI操作は人間の承認が必要"\n',
    )

    result = engine.evaluate(_env("cli-skill", "テスト"))

    assert result.allowed is False
    assert result.action == "require_approval"
    assert result.reason == "CLI操作は人間の承認が必要"


def test_block_outside_hours(tmp_path, monkeypatch) -> None:
    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 5, 8, 0, tzinfo=tz)

    monkeypatch.setattr("ai_agent_hub.policy.datetime", _FixedDateTime)
    engine = _policy(
        tmp_path,
        'rules:\n  - match:\n      intent: "llm-query"\n    action: block_if\n    condition: outside_hours\n    hours: "09:00-23:00"\n    timezone: "Asia/Tokyo"\n    reason: "この時間帯はLLM呼び出しが禁止されています"\n',
    )

    result = engine.evaluate(_env("llm-query"))

    assert result.allowed is False
    assert result.action == "block"


def test_block_daily_tokens_exceeded(tmp_path, monkeypatch) -> None:
    class _Store:
        def total_tokens_today(self) -> int:
            return 50001

    monkeypatch.setattr("ai_agent_hub.policy.TokenUsageStore", _Store)
    engine = _policy(
        tmp_path,
        'rules:\n  - match:\n      intent: "llm-query"\n    action: block_if\n    condition: daily_tokens_exceeded\n    limit: 50000\n    reason: "1日のトークン上限（50,000トークン）に達しました"\n',
    )

    result = engine.evaluate(_env("llm-query"))

    assert result.allowed is False
    assert result.action == "block"


def test_block_contains_keyword(tmp_path) -> None:
    engine = _policy(
        tmp_path,
        'rules:\n  - match:\n      intent: "*"\n    action: block_if\n    condition: contains_keyword\n    keywords:\n      - "secret_key"\n    reason: "機密情報を含むリクエストはブロックされました"\n',
    )

    result = engine.evaluate(_env("echo", "secret_key=abc"))

    assert result.allowed is False
    assert result.action == "block"


def test_pass_when_no_rules_match(tmp_path) -> None:
    engine = _policy(
        tmp_path,
        'rules:\n  - match:\n      intent: "payment"\n    action: require_approval\n    reason: "支払い操作は人間の承認が必要"\n',
    )

    result = engine.evaluate(_env("echo"))

    assert result.allowed is True
    assert result.action == "pass"


def test_wildcard_intent_match(tmp_path) -> None:
    engine = _policy(
        tmp_path,
        'rules:\n  - match:\n      intent: "*"\n    action: block_if\n    condition: contains_keyword\n    keywords:\n      - "パスワード"\n    reason: "機密情報を含むリクエストはブロックされました"\n',
    )

    result = engine.evaluate(_env("unknown", "パスワードを含む"))

    assert result.allowed is False
    assert result.action == "block"
