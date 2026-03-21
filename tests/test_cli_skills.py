from __future__ import annotations

import shutil
import subprocess

import pytest

from ai_agent_hub import Envelope
from ai_agent_hub.agent_worker import INTENT_HANDLERS, _handle_envelope
from ai_agent_hub.cli_skills import CliSkillRunner


@pytest.fixture
def runner() -> CliSkillRunner:
    return CliSkillRunner()


def _make_env(payload):
    return Envelope.new(
        envelope_type="command",
        sender="https://example.com/@alice",
        recipient="https://agent.local/@worker",
        payload=payload,
    )


def test_echo_skill_runs_successfully(runner: CliSkillRunner) -> None:
    result = runner.run("echo", ["hello"])

    assert result["exit_code"] == 0
    assert result["skill"] == "echo"
    assert result["args"] == ["hello"]
    assert result["output"] == "hello\n"


def test_non_allowlisted_skill_is_rejected(runner: CliSkillRunner) -> None:
    result = runner.run("python", ["-V"])

    assert result == {"error": "skill 'python' is not allowed", "exit_code": 1}


def test_timeout_is_enforced(runner: CliSkillRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_AGENT_HUB_SKILL_TIMEOUT", "1")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr("ai_agent_hub.cli_skills.subprocess.run", fake_run)

    result = runner.run("curl", ["https://example.com"])

    assert result == {"error": "timeout after 1s", "exit_code": 124}


def test_stdin_is_passed_correctly(runner: CliSkillRunner) -> None:
    result = runner.run("grep", ["world"], stdin="hello\nworld\n")

    assert result["exit_code"] == 0
    assert result["output"] == "world\n"


def test_cli_skill_intent_is_registered() -> None:
    assert "cli-skill" in INTENT_HANDLERS

    env = _make_env({"intent": "cli-skill", "skill": "echo", "args": ["hello"]})
    reply = _handle_envelope(env)

    assert reply is not None
    assert reply.payload == {
        "output": "hello\n",
        "exit_code": 0,
        "skill": "echo",
        "args": ["hello"],
    }


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")
def test_cli_pipeline_chains_commands() -> None:
    env = _make_env(
        {
            "intent": "cli-pipeline",
            "steps": [
                {"skill": "echo", "args": ['{"items":[{"title":"one"},{"title":"two"}]}']},
                {"skill": "jq", "args": ["-r", ".items[] | .title"]},
            ],
        }
    )

    reply = _handle_envelope(env)

    assert reply is not None
    assert reply.payload == {
        "output": "one\ntwo\n",
        "exit_code": 0,
        "skill": "jq",
        "args": ["-r", ".items[] | .title"],
    }
