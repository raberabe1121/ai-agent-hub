"""Envelope dataclass for AI Agent OS messaging."""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

AgentID = str
PayloadType = Union[str, Dict[str, Any]]


_AGENT_ID_PATTERN = re.compile(r"^https?://[^/]+/@[^/]+$")


def _validate_agent_id(agent_id: AgentID) -> None:
    if not _AGENT_ID_PATTERN.match(agent_id):
        raise ValueError(
            "Agent ID must follow ActivityPub style (e.g., https://domain/@name)."
        )


@dataclass
class Envelope:
    """
    AI Message Envelope v0.1.

    Attributes:
        id: Unique identifier (UUID recommended).
        envelope_type: Message category such as "follow", "post", "command", or "event".
        sender: ActivityPub-style agent identifier (e.g., https://example.com/@alice).
        recipient: ActivityPub-style agent identifier for the destination.
        payload: JSON-serializable object or plain text string.
        created_at: Timestamp for the envelope creation.
        context: Optional thread context identifier.
        in_reply_to: Optional identifier of the message being replied to.
        version: Envelope schema version (defaults to "v0.1").
    """

    id: str
    envelope_type: str
    sender: AgentID
    recipient: AgentID
    payload: PayloadType
    created_at: datetime = field(metadata={"json_name": "time"})
    context: Optional[str] = None
    in_reply_to: Optional[str] = field(default=None, metadata={"json_name": "inReplyTo"})
    version: str = "v0.1"

    def __post_init__(self) -> None:
        _validate_agent_id(self.sender)
        _validate_agent_id(self.recipient)
        if not isinstance(self.payload, (str, dict)):
            raise TypeError("payload must be a JSON object (dict) or text string")
        if isinstance(self.payload, dict):
            json.dumps(self.payload)  # ensure JSON-serializable
        if not isinstance(self.created_at, datetime):
            raise TypeError("created_at must be a datetime instance")
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=timezone.utc)

    @classmethod
    def new(
        cls,
        *,
        envelope_type: str,
        sender: AgentID,
        recipient: AgentID,
        payload: PayloadType,
        context: Optional[str] = None,
        in_reply_to: Optional[str] = None,
        version: str = "v0.1",
        created_at: Optional[datetime] = None,
        envelope_id: Optional[str] = None,
    ) -> "Envelope":
        """Convenience constructor with sensible defaults."""

        return cls(
            id=envelope_id or str(uuid.uuid4()),
            envelope_type=envelope_type,
            sender=sender,
            recipient=recipient,
            payload=payload,
            created_at=created_at or datetime.now(timezone.utc),
            context=context,
            in_reply_to=in_reply_to,
            version=version,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert envelope to a JSON-serializable dictionary."""

        return {
            "version": self.version,
            "id": self.id,
            "from": self.sender,
            "to": self.recipient,
            "type": self.envelope_type,
            "payload": self.payload,
            "time": self.created_at.isoformat(),
            "context": self.context,
            "inReplyTo": self.in_reply_to,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Envelope":
        """Create an envelope instance from a dictionary."""

        try:
            created_at_raw = data["time"]
        except KeyError as exc:
            raise KeyError("Missing required field 'time'") from exc

        created_at = (
            datetime.fromisoformat(created_at_raw)
            if isinstance(created_at_raw, str)
            else created_at_raw
        )

        return cls(
            id=data["id"],
            envelope_type=data["type"],
            sender=data["from"],
            recipient=data["to"],
            payload=data.get("payload"),
            created_at=created_at,
            context=data.get("context"),
            in_reply_to=data.get("inReplyTo"),
            version=data.get("version", "v0.1"),
        )

    def to_json(self, *, indent: Optional[int] = None) -> str:
        """Serialize the envelope to a JSON string."""

        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_json(cls, raw_json: str) -> "Envelope":
        """Deserialize a JSON string into an envelope instance."""

        data = json.loads(raw_json)
        if not isinstance(data, dict):
            raise ValueError("Envelope JSON must represent an object")
        return cls.from_dict(data)


__all__ = ["Envelope", "AgentID", "PayloadType"]
