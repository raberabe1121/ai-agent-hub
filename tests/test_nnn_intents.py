from __future__ import annotations

import re

import pytest

from ai_agent_hub import Envelope
import ai_agent_hub.agent_worker as agent_worker


def _make_env(payload) -> Envelope:
    return Envelope.new(
        envelope_type="command",
        sender="https://example.com/@alice",
        recipient="https://agent.local/@worker",
        payload=payload,
    )


class _FakeCliRunner:
    def __init__(self, outputs: dict[str, str]) -> None:
        self.outputs = outputs

    def run(self, *, skill: str, args: list[str], stdin=None):
        assert skill == "curl"
        assert stdin is None
        url = args[0]
        output = self.outputs.get(url, "")
        return {"exit_code": 0, "output": output}


def test_threat_scan_returns_16_cells(monkeypatch: pytest.MonkeyPatch) -> None:
    rss_xml = """
    <rss><channel>
      <item><title>cat abuse case</title><description>incident reported</description></item>
    </channel></rss>
    """
    monkeypatch.setattr(
        agent_worker,
        "CliSkillRunner",
        lambda: _FakeCliRunner({"https://news.ycombinator.com/rss": rss_xml}),
    )
    monkeypatch.setattr(
        agent_worker,
        "_llm_json_response",
        lambda prompt, model="gemma3:4b": {"level": 3, "label": "cat safety keyword spike", "active": False},
    )

    reply = agent_worker._handle_envelope(
        _make_env(
            {
                "intent": "threat-scan",
                "keywords": ["猫 虐待", "cat abuse", "猫捕食"],
                "languages": ["ja", "en", "zh", "vi"],
                "sector": "TOKYO-SECTOR",
            }
        )
    )

    assert reply is not None
    payload = reply.payload
    assert payload["sector"] == "TOKYO-SECTOR"
    assert payload["activityLabel"] == "MODERATE"
    assert payload["source"] == "ai-agent-hub-monitor"
    assert re.match(r"\d{4}-\d{2}-\d{2}T", payload["updatedAt"])
    assert len(payload["cells"]) == 16
    assert payload["cells"][0] == {
        "threatLevel": 3,
        "label": "cat safety keyword spike",
        "active": False,
    }
    assert payload["cells"][1] == {"threatLevel": 1, "label": "baseline", "active": False}


def test_cat_assessment_returns_llm_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_worker,
        "_llm_json_response",
        lambda prompt, model="gemma3:4b": {
            "score": 85,
            "verdict": "APPROVED",
            "patra_message": "在宅体制を維持せよ。",
            "strengths": ["在宅勤務で監視が可能"],
            "concerns": ["1LDKは狭いかもしれない"],
        },
    )

    reply = agent_worker._handle_envelope(
        _make_env(
            {
                "intent": "cat-assessment",
                "answers": {
                    "living_situation": "一人暮らし、1LDK",
                    "work_hours": "在宅勤務",
                    "experience": "猫を飼ったことがある",
                    "reason": "癒しと友達が欲しい",
                },
            }
        )
    )

    assert reply is not None
    assert reply.payload == {
        "score": 85,
        "verdict": "APPROVED",
        "patra_message": "在宅体制を維持せよ。",
        "strengths": ["在宅勤務で監視が可能"],
        "concerns": ["1LDKは狭いかもしれない"],
    }


def test_cat_assessment_returns_failed_when_answers_missing() -> None:
    reply = agent_worker._handle_envelope(_make_env({"intent": "cat-assessment"}))

    assert reply is not None
    assert reply.payload["error"] == "answers missing"
    assert reply.payload["status"] == "failed"
    assert isinstance(reply.payload["debug_keys"], list)
    assert isinstance(reply.payload["payload_keys"], list)


def test_cat_assessment_extracts_answers_from_nested_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_worker,
        "_llm_json_response",
        lambda prompt, model="gemma3:4b": {
            "score": 85,
            "verdict": "APPROVED",
            "patra_message": "ふむ、最低限の覚悟は見えたわ。",
            "strengths": ["在宅勤務で見守り時間を確保できる"],
            "concerns": ["1LDKの場合、脱走防止導線の確認が必要"],
        },
    )

    reply = agent_worker._handle_envelope(
        _make_env(
            {
                "intent": "cat-assessment",
                "payload": {
                    "answers": {
                        "living_situation": "1LDK",
                        "work_hours": "在宅勤務",
                        "experience": "猫経験あり",
                        "reason": "家族として迎えたい",
                    }
                },
            }
        )
    )

    assert reply is not None
    assert reply.payload["score"] == 85
    assert reply.payload["verdict"] == "APPROVED"


def test_cat_assessment_extracts_answers_from_text_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_worker,
        "_llm_json_response",
        lambda prompt, model="gemma3:4b": {
            "score": 85,
            "verdict": "APPROVED",
            "patra_message": "ふむ、最低限の覚悟は見えたわ。",
            "strengths": [],
            "concerns": [],
        },
    )
    reply = agent_worker._handle_envelope(
        _make_env(
            {
                "intent": "cat-assessment",
                "text": (
                    '{"payload":{"answers":{"living_situation":"1LDK","work_hours":"在宅勤務",'
                    '"experience":"猫経験あり","reason":"家族として迎えたい"}}}'
                ),
            }
        )
    )

    assert reply is not None
    assert reply.payload["score"] == 85
    assert reply.payload["verdict"] == "APPROVED"
