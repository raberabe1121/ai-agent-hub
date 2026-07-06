from __future__ import annotations

import pytest

from ai_agent_hub import Envelope
from ai_agent_hub.magi import MagiSystem, MagiVote
from ai_agent_hub.policy import PolicyEngine


async def _fake_vote(_self, persona_name: str, _system_prompt: str, _intent: str, _text: str) -> MagiVote:
    decisions = {
        "MELCHIOR": ("ALLOW", "技術的に妥当です"),
        "BALTHASAR": ("ALLOW", "安全上の問題は限定的です"),
        "CASPER": ("DENY", "不確実性があります"),
    }
    decision, reason = decisions[persona_name]
    return MagiVote(persona_name, decision, reason)


def test_magi_allow_when_2_allow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MagiSystem, "_call_persona", _fake_vote)

    result = MagiSystem().evaluate("cli-skill", "echo hello")

    assert result.final == "ALLOW"
    assert result.allow_count == 2
    assert result.deny_count == 1


def test_magi_deny_when_2_deny(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_deny(_self, persona_name: str, _system_prompt: str, _intent: str, _text: str) -> MagiVote:
        decision = "ALLOW" if persona_name == "MELCHIOR" else "DENY"
        return MagiVote(persona_name, decision, "理由")

    monkeypatch.setattr(MagiSystem, "_call_persona", fake_deny)

    result = MagiSystem().evaluate("cli-skill", "rm -rf /tmp/test")

    assert result.final == "DENY"
    assert result.allow_count == 1
    assert result.deny_count == 2


def test_magi_deny_on_parse_failure() -> None:
    vote = MagiSystem()._call_persona_sync("MELCHIOR", "system", "cli-skill", "echo hello")

    assert vote.decision == "DENY"


def test_magi_vote_in_policy(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Magi:
        def evaluate(self, intent: str, text: str):
            from ai_agent_hub.magi import MagiResult

            assert intent == "cli-skill"
            assert text == "echo hello"
            return MagiResult(
                final="ALLOW",
                votes=[
                    MagiVote("MELCHIOR", "ALLOW", "ok"),
                    MagiVote("BALTHASAR", "ALLOW", "ok"),
                    MagiVote("CASPER", "DENY", "risk"),
                ],
                allow_count=2,
                deny_count=1,
            )

    path = tmp_path / "policy.yaml"
    path.write_text(
        'rules:\n  - match:\n      intent: "cli-skill"\n    action: magi_vote\n    reason: "マギシステムで評価します"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MAGI_ENABLED", "true")
    monkeypatch.setattr("ai_agent_hub.magi.MagiSystem", _Magi)
    env = Envelope.new(
        envelope_type="email",
        sender="https://user.local/@me",
        recipient="https://ai-agent.local/@worker",
        payload={"intent": "cli-skill", "text": "echo hello"},
    )

    result = PolicyEngine(str(path)).evaluate(env)

    assert result.allowed is True
    assert result.action == "pass"
    assert result.magi_result is not None
