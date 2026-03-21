"""Circle/USDC payment gateway for AI Agent Hub."""
from __future__ import annotations

import importlib
import os
import uuid
from typing import Any

from ai_agent_hub import Envelope

CIRCLE_TRANSFERS_URL = "https://api.circle.com/v1/transfers"


class PaymentGateway:
    """Execute or simulate USDC payments through Circle."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        wallet_id: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("CIRCLE_API_KEY")
        self.wallet_id = (
            wallet_id if wallet_id is not None else os.environ.get("CIRCLE_WALLET_ID")
        )
        if enabled is None:
            enabled = os.environ.get("AI_AGENT_HUB_PAYMENT_ENABLED", "false").lower() == "true"
        self.enabled = enabled

    def execute(self, env: Envelope) -> dict[str, str]:
        """Execute a payment for the given envelope payload."""

        payload = env.payload
        if not isinstance(payload, dict):
            return {"status": "error", "error": "payload must be a JSON object"}

        amount = payload.get("amount")
        recipient = payload.get("recipient")
        description = payload.get("description")

        if amount in (None, ""):
            return {"status": "error", "error": "payload.amount is required"}
        if recipient in (None, ""):
            return {"status": "error", "error": "payload.recipient is required"}

        amount_str = str(amount)
        recipient_str = str(recipient)
        description_str = "" if description is None else str(description)

        if not self.enabled:
            return {
                "status": "dry_run",
                "amount": amount_str,
                "recipient": recipient_str,
            }

        if not self.api_key:
            return {"status": "error", "error": "CIRCLE_API_KEY is not set"}
        if not self.wallet_id:
            return {"status": "error", "error": "CIRCLE_WALLET_ID is not set"}

        request_body: dict[str, Any] = {
            "idempotencyKey": str(uuid.uuid4()),
            "source": {"type": "wallet", "id": self.wallet_id},
            "destination": {"type": "wallet", "id": recipient_str},
            "amount": {"amount": amount_str, "currency": "USD"},
        }
        if description_str:
            request_body["metadata"] = {"description": description_str}

        httpx = importlib.import_module("httpx")

        try:
            response = httpx.post(
                CIRCLE_TRANSFERS_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=request_body,
                timeout=30.0,
            )
            response.raise_for_status()
            response_data = response.json()
        except httpx.HTTPError as exc:
            return {"status": "error", "error": str(exc)}

        transaction_id = _extract_transaction_id(response_data)
        if not transaction_id:
            return {"status": "error", "error": "Circle response missing transaction id"}

        return {
            "status": "success",
            "transaction_id": transaction_id,
            "amount": amount_str,
        }


def _extract_transaction_id(response_data: Any) -> str | None:
    if not isinstance(response_data, dict):
        return None

    data = response_data.get("data")
    if isinstance(data, dict):
        transaction_id = data.get("id") or data.get("transactionHash")
        return str(transaction_id) if transaction_id else None

    if isinstance(data, list) and data:
        item = data[0]
        if isinstance(item, dict):
            transaction_id = item.get("id") or item.get("transactionHash")
            return str(transaction_id) if transaction_id else None

    return None


__all__ = ["PaymentGateway", "CIRCLE_TRANSFERS_URL"]
