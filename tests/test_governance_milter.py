from __future__ import annotations

import logging
from email.message import EmailMessage

from ai_agent_hub.governance_milter import evaluate_policy


def _make_message(
    *,
    policy: str | None = None,
    workflow: str | None = None,
    cost_center: str | None = None,
    body: str = '{"payload": {"intent": "ping"}}',
) -> EmailMessage:
    message = EmailMessage()
    if policy is not None:
        message["X-Agent-Policy"] = policy
    if workflow is not None:
        message["X-Agent-Workflow"] = workflow
    if cost_center is not None:
        message["X-Agent-Cost-Center"] = cost_center
    message.set_content(body, subtype="plain", charset="utf-8")
    return message


def test_confidential_block_rejects_external_sender(caplog) -> None:
    message = _make_message(policy="confidential=block")

    with caplog.at_level(logging.INFO):
        decision = evaluate_policy(
            sender="alice@example.com",
            message=message,
            allowed_domain="agent.local",
        )

    assert decision.accepted is False
    assert "confidential delivery blocked" in decision.reason
    assert "rejected sender=alice@example.com" in caplog.text


def test_message_without_policy_header_is_accepted(caplog) -> None:
    message = _make_message()

    with caplog.at_level(logging.INFO):
        decision = evaluate_policy(
            sender="agent@agent.local",
            message=message,
            allowed_domain="agent.local",
        )

    assert decision.accepted is True
    assert decision.reason == "accepted"
    assert "accepted sender=agent@agent.local" in caplog.text


def test_workflow_requires_spec_approval_flag(caplog) -> None:
    message = _make_message(
        workflow="spec-approval-required=true",
        body='{"payload": {"intent": "implement-feature"}}',
    )

    with caplog.at_level(logging.INFO):
        decision = evaluate_policy(
            sender="agent@agent.local",
            message=message,
            allowed_domain="agent.local",
        )

    assert decision.accepted is False
    assert "spec approval required" in decision.reason
    assert "rejected sender=agent@agent.local" in caplog.text


def test_logs_record_cost_center_and_decision(caplog) -> None:
    message = _make_message(cost_center="rnd-42")

    with caplog.at_level(logging.INFO):
        decision = evaluate_policy(
            sender="agent@agent.local",
            message=message,
            allowed_domain="agent.local",
        )

    assert decision.accepted is True
    assert "cost center=rnd-42 sender=agent@agent.local" in caplog.text
    assert "evaluating sender=agent@agent.local" in caplog.text
    assert "accepted sender=agent@agent.local" in caplog.text
