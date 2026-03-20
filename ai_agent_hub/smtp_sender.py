"""SMTP sender for AI Agent Hub envelopes."""
from __future__ import annotations

import json
import smtplib
from email.message import EmailMessage
from email.utils import format_datetime

from ai_agent_hub import Envelope


def _envelope_to_mime(env: Envelope) -> EmailMessage:
    """Convert an envelope to a MIME email message."""

    msg = EmailMessage()
    # Preserve ActivityPub IDs in headers while supplying email addr-spec for transport
    msg["From"] = f"{env.sender} <agent@localhost>"
    msg["To"] = f"{env.recipient} <worker@localhost>"
    msg["Subject"] = f"AI-Agent-Hub: {env.envelope_type}"
    msg["Date"] = format_datetime(env.created_at)
    msg["Message-ID"] = f"<{env.id}@ai-agent-hub>"

    body = json.dumps(
        {
            "payload": env.payload,
            "context": env.context,
            "inReplyTo": env.in_reply_to,
            "time": env.created_at.isoformat(),
            "version": env.version,
        },
        ensure_ascii=False,
    )
    msg.set_content(body, subtype="plain", charset="utf-8")
    return msg


def send_envelope_via_smtp(env: Envelope) -> None:
    """Send the envelope to Postfix via SMTP on localhost."""

    mime_message = _envelope_to_mime(env)

    # SMTP envelope addresses must be addr-spec, not ActivityPub IDs.
    smtp_from = "agent@localhost"
    smtp_to = ["worker@localhost"]

    with smtplib.SMTP("localhost", 25) as smtp:
        smtp.sendmail(smtp_from, smtp_to, mime_message.as_string())


__all__ = ["send_envelope_via_smtp", "_envelope_to_mime"]
