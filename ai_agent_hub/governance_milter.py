"""Postfix governance milter enforcing AI Agent Hub delivery policies."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from email.message import Message
from email.parser import BytesParser
from email.policy import default
from typing import Any

try:  # pragma: no cover - exercised in integration environments
    import Milter
except ImportError:  # pragma: no cover - fallback for test environments
    Milter = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_MILTER_PORT = 8025
DEFAULT_ALLOWED_DOMAIN = "agent.local"
_STATUS_ACCEPT = getattr(Milter, "ACCEPT", 0)
_STATUS_CONTINUE = getattr(Milter, "CONTINUE", 0)
_STATUS_REJECT = getattr(Milter, "REJECT", 1)


@dataclass(frozen=True)
class PolicyDecision:
    """Outcome returned by the governance policy engine."""

    accepted: bool
    reason: str = "accepted"


def get_milter_port() -> int:
    """Return the listening TCP port for the governance milter."""

    raw_port = os.getenv("AI_AGENT_HUB_MILTER_PORT", str(DEFAULT_MILTER_PORT))
    try:
        return int(raw_port)
    except ValueError as exc:
        raise ValueError(
            f"AI_AGENT_HUB_MILTER_PORT must be an integer, got {raw_port!r}"
        ) from exc


def get_allowed_domain() -> str:
    """Return the organization domain allowed for confidential traffic."""

    return os.getenv("AI_AGENT_HUB_ALLOWED_DOMAIN", DEFAULT_ALLOWED_DOMAIN).strip() or DEFAULT_ALLOWED_DOMAIN


def parse_policy_header(raw_value: str | None) -> dict[str, str]:
    """Parse comma/semicolon-separated policy headers into a dictionary."""

    if not raw_value:
        return {}

    parsed: dict[str, str] = {}
    for chunk in re.split(r"[,;]", raw_value):
        item = chunk.strip()
        if not item:
            continue
        if "=" in item:
            key, value = item.split("=", 1)
            parsed[key.strip().lower()] = value.strip().lower()
        else:
            parsed[item.lower()] = "true"
    return parsed


def parse_message(raw_message: bytes) -> Message:
    """Parse a raw MIME message into an ``email.message.Message`` instance."""

    return BytesParser(policy=default).parsebytes(raw_message)


def extract_payload_data(message: Message) -> dict[str, Any]:
    """Extract JSON payload data from the MIME body when available."""

    if message.is_multipart():
        body_parts = [
            part.get_content()
            for part in message.walk()
            if part.get_content_type() == "text/plain"
        ]
        raw_content = "\n".join(part for part in body_parts if isinstance(part, str))
    else:
        raw_content = message.get_content()

    if isinstance(raw_content, bytes):
        raw_content = raw_content.decode(errors="replace")

    if not isinstance(raw_content, str):
        return {}

    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError:
        logger.info("governance_milter: body is not JSON; skipping payload flag checks")
        return {}

    if isinstance(payload, dict):
        return payload
    return {}


def extract_spec_approved_flag(payload_data: dict[str, Any]) -> bool:
    """Return whether the message payload marks the spec as approved."""

    candidates = [payload_data]
    nested_payload = payload_data.get("payload")
    if isinstance(nested_payload, dict):
        candidates.append(nested_payload)

    for candidate in candidates:
        flag = candidate.get("spec_approved")
        if isinstance(flag, bool):
            return flag
        if isinstance(flag, str) and flag.strip().lower() == "true":
            return True
    return False


def extract_sender_domain(sender: str | None) -> str:
    """Extract the domain portion from an SMTP envelope sender."""

    if not sender:
        return ""

    normalized = sender.strip().strip("<>")
    if "@" not in normalized:
        return ""
    return normalized.rsplit("@", 1)[-1].lower()


def evaluate_policy(
    *,
    sender: str | None,
    message: Message,
    allowed_domain: str | None = None,
) -> PolicyDecision:
    """Evaluate governance headers and payload flags for a message."""

    normalized_allowed_domain = (allowed_domain or get_allowed_domain()).lower()
    policy_values = parse_policy_header(message.get("X-Agent-Policy"))
    workflow_values = parse_policy_header(message.get("X-Agent-Workflow"))
    cost_center = message.get("X-Agent-Cost-Center")
    if cost_center:
        logger.info("governance_milter: cost center=%s sender=%s", cost_center, sender)

    sender_domain = extract_sender_domain(sender)
    logger.info(
        "governance_milter: evaluating sender=%s sender_domain=%s allowed_domain=%s policy=%s workflow=%s",
        sender,
        sender_domain,
        normalized_allowed_domain,
        policy_values,
        workflow_values,
    )

    if policy_values.get("confidential") == "block" and sender_domain != normalized_allowed_domain:
        reason = (
            f"confidential delivery blocked for external sender domain '{sender_domain or 'unknown'}'"
        )
        logger.warning("governance_milter: rejected sender=%s reason=%s", sender, reason)
        return PolicyDecision(accepted=False, reason=reason)

    if workflow_values.get("spec-approval-required") == "true":
        payload_data = extract_payload_data(message)
        if not extract_spec_approved_flag(payload_data):
            reason = "spec approval required but payload.spec_approved flag is missing"
            logger.warning("governance_milter: rejected sender=%s reason=%s", sender, reason)
            return PolicyDecision(accepted=False, reason=reason)

    logger.info("governance_milter: accepted sender=%s", sender)
    return const_decision_accept()


def const_decision_accept() -> PolicyDecision:
    """Return the canonical accept decision."""

    return PolicyDecision(accepted=True, reason="accepted")


if Milter is not None:  # pragma: no branch

    class GovernanceMilter(Milter.Base):
        """Postfix milter implementation for AI Agent Hub governance checks."""

        def __init__(self) -> None:
            self.mail_from = ""
            self._headers: list[tuple[str, str]] = []
            self._body_chunks: list[bytes] = []

        def connect(self, hostname: str, family: Any, hostaddr: Any) -> int:
            logger.info(
                "governance_milter: connect hostname=%s family=%s hostaddr=%s",
                hostname,
                family,
                hostaddr,
            )
            return _STATUS_CONTINUE

        def envfrom(self, mailfrom: str, *args: str) -> int:
            self.mail_from = mailfrom
            logger.info("governance_milter: envfrom sender=%s args=%s", mailfrom, args)
            return _STATUS_CONTINUE

        def header(self, name: str, value: str) -> int:
            self._headers.append((name, value))
            logger.info("governance_milter: header %s=%s", name, value)
            return _STATUS_CONTINUE

        def body(self, chunk: bytes) -> int:
            self._body_chunks.append(chunk)
            return _STATUS_CONTINUE

        def eom(self) -> int:
            message = parse_message(self._build_message_bytes())
            decision = evaluate_policy(sender=self.mail_from, message=message)
            if decision.accepted:
                return _STATUS_ACCEPT

            self.setreply("550", "5.7.1", f"Policy violation: {decision.reason}")
            return _STATUS_REJECT

        def close(self) -> int:
            logger.info("governance_milter: closing session for sender=%s", self.mail_from)
            self.mail_from = ""
            self._headers.clear()
            self._body_chunks.clear()
            return _STATUS_CONTINUE

        def _build_message_bytes(self) -> bytes:
            header_blob = b"".join(
                f"{name}: {value}\r\n".encode("utf-8", errors="replace")
                for name, value in self._headers
            )
            body_blob = b"".join(self._body_chunks)
            return header_blob + b"\r\n" + body_blob

else:

    class GovernanceMilter:  # pragma: no cover - test fallback only
        """Fallback placeholder used when ``pymilter`` is unavailable."""

        def __init__(self) -> None:
            raise RuntimeError(
                "pymilter is required to run GovernanceMilter; install it with `pip install pymilter`."
            )


def run_server() -> None:
    """Start the governance milter server."""

    if Milter is None:
        raise RuntimeError(
            "pymilter is required to run the governance milter; install it with `pip install pymilter`."
        )

    logging.basicConfig(
        level=os.getenv("AI_AGENT_HUB_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    port = get_milter_port()
    socket_spec = f"inet:{port}@localhost"
    logger.info(
        "governance_milter: starting server on %s with allowed_domain=%s",
        socket_spec,
        get_allowed_domain(),
    )
    Milter.factory = GovernanceMilter
    Milter.runmilter("governance_milter", socket_spec, 600)


if __name__ == "__main__":
    run_server()
