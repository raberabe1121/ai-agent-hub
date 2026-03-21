"""CLI skill execution helpers for agent intents."""
from __future__ import annotations

import os
import subprocess
from typing import Any

ALLOWED_SKILLS: dict[str, list[str]] = {
    "gh": ["gh"],
    "jq": ["jq"],
    "grep": ["grep"],
    "curl": ["curl"],
    "echo": ["echo"],
}


class CliSkillRunner:
    """Run allowlisted CLI skills as subprocesses."""

    @staticmethod
    def _default_timeout() -> int:
        raw_timeout = os.environ.get("AI_AGENT_HUB_SKILL_TIMEOUT", "30")
        try:
            return int(raw_timeout)
        except (TypeError, ValueError):
            return 30

    def run(
        self,
        skill: str,
        args: list[str],
        stdin: str | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        command = ALLOWED_SKILLS.get(skill)
        if command is None:
            return {"error": f"skill '{skill}' is not allowed", "exit_code": 1}

        effective_timeout = self._default_timeout()
        if timeout != 30:
            effective_timeout = timeout

        try:
            completed = subprocess.run(
                [*command, *args],
                input=stdin,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"error": f"timeout after {effective_timeout}s", "exit_code": 124}
        except OSError as exc:
            return {
                "error": str(exc),
                "exit_code": 1,
                "skill": skill,
                "args": args,
            }

        output = completed.stdout
        if completed.stderr:
            output = f"{output}{completed.stderr}"

        return {
            "output": output,
            "exit_code": completed.returncode,
            "skill": skill,
            "args": args,
        }
