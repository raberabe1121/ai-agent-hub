"""Policy engine for evaluating AI Agent Hub envelopes."""
from __future__ import annotations

from dataclasses import dataclass
import os
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - dependency fallback
    yaml = None

from ai_agent_hub.token_usage import TokenUsageStore


@dataclass
class PolicyResult:
    allowed: bool
    action: str  # "pass" | "block" | "require_approval"
    reason: str = ""
    matched_rule: dict | None = None
    magi_result: Any | None = None


class PolicyEngine:
    """Evaluate envelopes against rules loaded from a YAML policy file."""

    def __init__(self, policy_path: str = "policy.yaml") -> None:
        self.policy_path = policy_path
        self.rules = self._load_rules(policy_path)

    def _load_rules(self, policy_path: str) -> list[dict[str, Any]]:
        path = Path(policy_path)
        if not path.exists():
            return []
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) if yaml is not None else self._parse_simple_policy(raw)
        data = data or {}
        rules = data.get("rules") if isinstance(data, dict) else None
        if not isinstance(rules, list):
            return []
        return [rule for rule in rules if isinstance(rule, dict)]


    def _parse_simple_policy(self, raw: str) -> dict[str, Any]:
        """Parse the small policy.yaml subset used by the default policy."""
        rules: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        current_list_key: str | None = None
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped == "rules:":
                continue
            if stripped == "- match:":
                current = {"match": {}}
                rules.append(current)
                current_list_key = None
                continue
            if stripped.startswith("- ") and current_list_key and current is not None:
                current.setdefault(current_list_key, []).append(self._parse_scalar(stripped[2:].strip()))
                continue
            if ":" not in stripped or current is None:
                continue
            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()
            if not value:
                current_list_key = key
                current[key] = []
                continue
            current_list_key = None
            target = current["match"] if key == "intent" and "action" not in current else current
            target[key] = self._parse_scalar(value)
        return {"rules": rules}

    def _parse_scalar(self, value: str) -> Any:
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            return value[1:-1]
        try:
            return int(value)
        except ValueError:
            return value

    def evaluate(self, envelope: Any) -> PolicyResult:
        """Return the first matching rule result, or pass when no rule matches."""

        for rule in self.rules:
            if not self._matches(rule.get("match"), envelope):
                continue

            action = rule.get("action")
            reason = str(rule.get("reason") or "")
            if action == "require_approval":
                return PolicyResult(False, "require_approval", reason, rule)
            if action == "magi_vote":
                return self._evaluate_magi_vote(rule, envelope)
            if action == "block_if":
                return self._evaluate_block_if(rule, envelope)

        return PolicyResult(True, "pass")


    def _evaluate_magi_vote(self, rule: dict[str, Any], envelope: Any) -> PolicyResult:
        reason = str(rule.get("reason") or "")
        if os.environ.get("MAGI_ENABLED", "false").strip().lower() != "true":
            return PolicyResult(False, "require_approval", reason, rule)

        from ai_agent_hub.magi import MagiSystem

        intent = self._intent(envelope) or ""
        text = self._text(envelope)
        magi_result = MagiSystem().evaluate(intent, text)
        if magi_result.final == "ALLOW":
            return PolicyResult(True, "pass", matched_rule=rule, magi_result=magi_result)
        vote_reasons = "; ".join(
            f"{vote.persona}: {vote.decision} - {vote.reason}" for vote in magi_result.votes
        )
        return PolicyResult(False, "block", vote_reasons or reason, rule, magi_result)

    def _matches(self, match: Any, envelope: Any) -> bool:
        if not isinstance(match, dict):
            return False
        rule_intent = match.get("intent")
        envelope_intent = self._intent(envelope)
        return rule_intent == "*" or rule_intent == envelope_intent

    def _evaluate_block_if(self, rule: dict[str, Any], envelope: Any) -> PolicyResult:
        condition = rule.get("condition")
        reason = str(rule.get("reason") or "")
        should_block = False

        if condition == "daily_tokens_exceeded":
            limit = int(rule.get("limit") or 0)
            should_block = TokenUsageStore().total_tokens_today() > limit
        elif condition == "outside_hours":
            should_block = self._is_outside_hours(rule)
        elif condition == "contains_keyword":
            should_block = self._contains_keyword(rule, envelope)

        if should_block:
            return PolicyResult(False, "block", reason, rule)
        return PolicyResult(True, "pass", matched_rule=rule)

    def _intent(self, envelope: Any) -> str | None:
        payload = getattr(envelope, "payload", None)
        if isinstance(payload, dict):
            value = payload.get("intent")
            return str(value) if value is not None else None
        return None

    def _text(self, envelope: Any) -> str:
        payload = getattr(envelope, "payload", None)
        if isinstance(payload, dict):
            value = payload.get("text")
            return str(value) if value is not None else ""
        return ""

    def _is_outside_hours(self, rule: dict[str, Any]) -> bool:
        hours = str(rule.get("hours") or "")
        start_raw, end_raw = hours.split("-", 1)
        start = time.fromisoformat(start_raw)
        end = time.fromisoformat(end_raw)
        tz_name = str(rule.get("timezone") or "UTC")
        now = datetime.now(ZoneInfo(tz_name)).time()
        if start <= end:
            return not (start <= now <= end)
        return not (now >= start or now <= end)

    def _contains_keyword(self, rule: dict[str, Any], envelope: Any) -> bool:
        payload = getattr(envelope, "payload", None)
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str):
            return False
        keywords = rule.get("keywords")
        if not isinstance(keywords, list):
            return False
        return any(isinstance(keyword, str) and keyword in text for keyword in keywords)
