from __future__ import annotations

import pytest

from ai_agent_hub import Envelope
from ai_agent_hub.agent_worker import INTENT_HANDLERS, _handle_envelope
from ai_agent_hub.payment_gateway import PaymentGateway


BASE_SENDER = "https://example.com/@alice"
BASE_RECIPIENT = "https://agent.local/@worker"
PAYMENT_RECIPIENT = "https://pay.example/@merchant"


def _make_env(payload: dict) -> Envelope:
    return Envelope.new(
        envelope_type="command",
        sender=BASE_SENDER,
        recipient=BASE_RECIPIENT,
        payload=payload,
    )


def test_payment_gateway_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_AGENT_HUB_PAYMENT_ENABLED", "false")
    monkeypatch.setenv("CIRCLE_API_KEY", "test-key")

    gateway = PaymentGateway()

    result = gateway.execute(
        _make_env(
            {
                "intent": "payment",
                "amount": "12.50",
                "recipient": PAYMENT_RECIPIENT,
                "description": "Test transfer",
            }
        )
    )

    assert result == {
        "status": "dry_run",
        "amount": "12.50",
        "recipient": PAYMENT_RECIPIENT,
    }


def test_payment_intent_returns_error_when_api_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CIRCLE_API_KEY", raising=False)

    reply = _handle_envelope(
        _make_env(
            {
                "intent": "payment",
                "amount": "2.00",
                "recipient": PAYMENT_RECIPIENT,
                "description": "Missing API key",
            }
        )
    )

    assert reply is not None
    assert reply.payload == {"error": "CIRCLE_API_KEY is not set"}


def test_payment_gateway_requires_amount() -> None:
    gateway = PaymentGateway(api_key="test-key", enabled=False)

    result = gateway.execute(
        _make_env(
            {
                "intent": "payment",
                "recipient": PAYMENT_RECIPIENT,
                "description": "Missing amount",
            }
        )
    )

    assert result == {"status": "error", "error": "payload.amount is required"}


def test_payment_gateway_calls_circle_api(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"id": "txn_123"}}

    class FakeHttpx:
        HTTPError = RuntimeError

        @staticmethod
        def post(url: str, *, headers: dict[str, str], json: dict[str, object], timeout: float):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            captured["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr("ai_agent_hub.payment_gateway.importlib.import_module", lambda name: FakeHttpx)

    gateway = PaymentGateway(api_key="circle-key", wallet_id="wallet-123", enabled=True)
    result = gateway.execute(
        _make_env(
            {
                "intent": "payment",
                "amount": "9.99",
                "recipient": PAYMENT_RECIPIENT,
                "description": "Paid via Circle",
            }
        )
    )

    assert result == {
        "status": "success",
        "transaction_id": "txn_123",
        "amount": "9.99",
    }
    assert captured["url"] == "https://api.circle.com/v1/transfers"
    assert captured["headers"] == {
        "Authorization": "Bearer circle-key",
        "Content-Type": "application/json",
    }
    assert captured["timeout"] == 30.0
    assert captured["json"] == {
        "idempotencyKey": captured["json"]["idempotencyKey"],
        "source": {"type": "wallet", "id": "wallet-123"},
        "destination": {"type": "wallet", "id": PAYMENT_RECIPIENT},
        "amount": {"amount": "9.99", "currency": "USD"},
        "metadata": {"description": "Paid via Circle"},
    }


def test_payment_intent_is_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CIRCLE_API_KEY", "test-key")

    assert "payment" in INTENT_HANDLERS

    reply = _handle_envelope(
        _make_env(
            {
                "intent": "payment",
                "amount": "5.00",
                "recipient": PAYMENT_RECIPIENT,
                "description": "Intent registration check",
            }
        )
    )

    assert reply is not None
    assert reply.payload == {
        "status": "dry_run",
        "amount": "5.00",
        "recipient": PAYMENT_RECIPIENT,
    }


def test_payment_header_triggers_payment_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CIRCLE_API_KEY", "test-key")

    reply = _handle_envelope(
        _make_env(
            {
                "headers": {"X-Agent-Payment-Required": "true"},
                "amount": "3.50",
                "recipient": PAYMENT_RECIPIENT,
                "description": "Header-detected payment",
            }
        )
    )

    assert reply is not None
    assert reply.payload == {
        "status": "dry_run",
        "amount": "3.50",
        "recipient": PAYMENT_RECIPIENT,
    }
