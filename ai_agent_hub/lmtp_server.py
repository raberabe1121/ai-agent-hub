"""Asyncio-based LMTP server for AI Agent Hub."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from email import message_from_bytes
from typing import Awaitable, Callable

from ai_agent_hub import Envelope
# Helper functions migrated from the former aiosmtpd handler:
# extract_sender, extract_recipient, extract_body, save_envelope
from ai_agent_hub.lmtp_handler import (
    extract_body,
    extract_recipient,
    extract_sender,
    save_envelope,
)

DEBUG_LOG = "/tmp/lmtp_debug.log"


def debug(*args: object) -> None:
    with open(DEBUG_LOG, "a") as f:
        print(*args, file=f, flush=True)

ResponseWriter = Callable[[str], Awaitable[None]]


class LMTPServer:
    """Minimal LMTP server using asyncio streams.

    The server accepts a single message at a time and converts it into an
    Envelope saved to the queue directory. Designed for compatibility with
    Postfix using ``lmtp:inet:localhost:8024``.
    """

    def __init__(self, port: int = 8024) -> None:
        self.port = port
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client,
            host="127.0.0.1",
            port=self.port,
            reuse_address=True,
        )
        addr = self._server.sockets[0].getsockname() if self._server.sockets else "unknown"
        debug(f"✅ LMTP server bound to {addr}")
        print(f"AI Agent Hub asyncio LMTP server listening on {addr}")
        print(
            f"LMTP server startup OK. Listening on 127.0.0.1:{self.port}",
            flush=True,
        )

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async def write_response(line: str) -> None:
            writer.write((line + "\r\n").encode())
            await writer.drain()

        await write_response("220 AI-Agent-Hub LMTP server ready")

        mail_from: str | None = None
        recipients: list[str] = []
        in_data_mode = False
        data_lines: list[bytes] = []

        while not reader.at_eof():
            raw_line = await reader.readline()
            if raw_line == b"":
                break
            if in_data_mode:
                if raw_line.rstrip() == b".":
                    await self._process_message(data_lines, mail_from, recipients, write_response)
                    in_data_mode = False
                    data_lines = []
                    mail_from = None
                    recipients = []
                    continue
                if raw_line.startswith(b".."):
                    raw_line = raw_line[1:]
                data_lines.append(raw_line)
                continue

            line = raw_line.decode(errors="replace").strip()
            upper = line.upper()

            if upper.startswith("LHLO"):
                await write_response("250 OK")
            elif upper.startswith("MAIL FROM:"):
                mail_from = line[10:].strip()
                await write_response("250 OK")
            elif upper.startswith("RCPT TO:"):
                recipients.append(line[8:].strip())
                await write_response("250 OK")
            elif upper == "DATA":
                in_data_mode = True
                data_lines = []
                await write_response("354 Start mail input; end with <CRLF>.<CRLF>")
            elif upper == "QUIT":
                await write_response("221 Bye")
                break
            else:
                await write_response("500 Unknown command")

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    async def _process_message(
        self,
        data_lines: list[bytes],
        mail_from: str | None,
        recipients: list[str],
        write_response: ResponseWriter,
    ) -> None:
        message_id = str(uuid.uuid4())
        raw_bytes = b"".join(data_lines)

        debug("----- PROCESS MESSAGE -----")
        debug("raw_bytes =", data_lines)
        debug("mail_from =", mail_from)
        debug("recipients =", recipients)

        try:
            # Parse MIME
            msg = message_from_bytes(raw_bytes)

            # Extract AP IDs
            sender = extract_sender(msg)
            recipient = extract_recipient(msg)

            # Extract payload and optional metadata
            payload_data = extract_body(msg)
            context = None
            in_reply_to = None
            created_at = datetime.now(timezone.utc)

            if isinstance(payload_data, dict):
                context = payload_data.get("context")
                in_reply_to = payload_data.get("inReplyTo") or payload_data.get(
                    "in_reply_to"
                )

                time_value = payload_data.get("time")
                if isinstance(time_value, str):
                    try:
                        parsed = datetime.fromisoformat(time_value)
                        if parsed.tzinfo is None:
                            parsed = parsed.replace(tzinfo=timezone.utc)
                        created_at = parsed
                    except ValueError:
                        created_at = datetime.now(timezone.utc)

                payload = payload_data.get("payload", payload_data)
            else:
                payload = payload_data

            # Create envelope
            env = Envelope.new(
                envelope_type="email",
                sender=sender,
                recipient=recipient,
                payload=payload,
                context=context,
                in_reply_to=in_reply_to,
                created_at=created_at,
            )

            # Save to queue
            save_envelope(env)

            debug("SAVED ENVELOPE:", env.id)
            debug("QUEUE PATH:", env.sender, env.recipient)

            await write_response(f"250 OK queued as {message_id}")

        except Exception as exc:
            # 🔥 FULL traceback を systemd に流す
            import traceback

            tb = traceback.format_exc()
            debug("ERROR in LMTP processing:", tb)

            await write_response("451 Requested action aborted: processing error")


def main() -> None:
    debug("=== LMTP SERVER STARTING ===")
    debug("Python:", sys.version)
    debug("CWD:", os.getcwd())
    debug("PYTHONPATH:", sys.path)
    debug("QUEUE_DIR env:", os.environ.get("AI_AGENT_HUB_QUEUE_DIR"))

    try:
        server = LMTPServer()
        debug("LMTPServer instance created")
        asyncio.run(server.serve_forever())
    except Exception as exc:
        debug("FATAL ERROR:", exc)
        import traceback

        debug(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
