"""AI Agent Hub Python SDK."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover - fallback for minimal environments
    class _HTTPXError(Exception):
        pass

    class _ConnectError(_HTTPXError):
        pass

    class _TimeoutException(_HTTPXError):
        pass

    class _ReadTimeout(_TimeoutException):
        pass

    class _Request:
        def __init__(self, method: str, url: str):
            self.method = method
            self.url = url

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._args = args
            self._kwargs = kwargs

        def request(self, *args: Any, **kwargs: Any):
            raise RuntimeError("httpx is not installed")

    class _HTTPXModule:
        Client = _Client
        ConnectError = _ConnectError
        TimeoutException = _TimeoutException
        ReadTimeout = _ReadTimeout
        HTTPError = _HTTPXError
        Request = _Request
        Response = Any

    httpx = _HTTPXModule()


class AgentHubError(Exception):
    """Base exception for AI Agent Hub SDK errors."""


class AgentHubConnectionError(AgentHubError):
    """Raised when SDK cannot connect to the API server."""


class AgentHubTimeoutError(AgentHubError):
    """Raised when API communication times out."""


@dataclass
class SendResult:
    envelope_id: str
    payload: dict[str, Any] | None
    status: str  # "ok" | "timeout" | "queued"

    def __str__(self) -> str:
        return f"SendResult(id={self.envelope_id}, payload={self.payload})"


@dataclass
class LogEntry:
    id: str
    time: str | None
    intent: str | None
    sender: str | None
    recipient: str | None
    payload: dict[str, Any] | str | None
    in_reply_to: str | None
    context: str | None


@dataclass
class ApprovalEntry:
    approval_id: str
    description: str
    approver: str
    status: str
    created_at: str | None = None
    decided_at: str | None = None


class AgentHub:
    """Client for AI Agent Hub REST API."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("AI_AGENT_HUB_URL") or "http://localhost:8080").rstrip("/")
        self._client = httpx.Client(base_url=self.base_url)

    def _request(
        self,
        method: str,
        path: str,
        *,
        timeout: float | None = None,
        allow_statuses: set[int] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            response = self._client.request(method, path, timeout=timeout, **kwargs)
        except httpx.ConnectError as exc:
            raise AgentHubConnectionError(f"Could not connect to API server: {self.base_url}") from exc
        except httpx.TimeoutException as exc:
            raise AgentHubTimeoutError(f"Request timed out: {method} {path}") from exc
        except httpx.HTTPError as exc:
            raise AgentHubError(f"HTTP error while calling API: {exc}") from exc

        allowed = allow_statuses or set()
        if response.status_code in allowed:
            return response
        if response.status_code >= 400:
            detail = response.text
            try:
                data = response.json()
                if isinstance(data, dict) and "detail" in data:
                    detail = str(data["detail"])
            except ValueError:
                pass
            raise AgentHubError(f"API error ({response.status_code}) for {method} {path}: {detail}")

        return response

    def _send_request(self, body: dict[str, Any], wait: bool = True, timeout: int = 30) -> SendResult:
        response = self._request("POST", "/envelopes", json=body, timeout=timeout)
        data = response.json()
        envelope_id = data["envelope_id"]

        if not wait:
            return SendResult(envelope_id=envelope_id, payload=None, status="queued")

        reply = self.get_reply(envelope_id, timeout=timeout)
        if reply is None:
            return SendResult(envelope_id=envelope_id, payload=None, status="timeout")

        payload = reply.get("payload") if isinstance(reply, dict) else None
        payload_dict = payload if isinstance(payload, dict) else None
        return SendResult(envelope_id=envelope_id, payload=payload_dict, status="ok")

    def send(
        self,
        intent: str,
        text: str | None = None,
        model: str | None = None,
        wait: bool = True,
        timeout: int = 30,
    ) -> SendResult:
        body: dict[str, Any] = {"intent": intent}
        if text is not None:
            body["text"] = text
        if model is not None:
            body["model"] = model
        return self._send_request(body=body, wait=wait, timeout=timeout)

    def get_reply(self, envelope_id: str, timeout: int = 30) -> dict[str, Any] | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = max(1, int(deadline - time.time()))
            response = self._request(
                "GET",
                f"/envelopes/{envelope_id}/reply",
                params={"timeout_sec": min(remaining, 1)},
                timeout=min(remaining, 2),
                allow_statuses={404},
            )
            if response.status_code == 200:
                return response.json()
            time.sleep(0.2)

        return None

    def logs(self, limit: int = 20, intent: str | None = None, thread_id: str | None = None) -> list[LogEntry]:
        params: dict[str, Any] = {"limit": limit}
        if intent is not None:
            params["intent"] = intent
        if thread_id is not None:
            params["thread_id"] = thread_id

        response = self._request("GET", "/logs", params=params)
        items = response.json().get("logs", [])
        return [
            LogEntry(
                id=item.get("id", ""),
                time=item.get("time"),
                intent=item.get("intent"),
                sender=item.get("from"),
                recipient=item.get("to"),
                payload=item.get("payload"),
                in_reply_to=item.get("in_reply_to"),
                context=item.get("context"),
            )
            for item in items
        ]

    def pending_approvals(self) -> list[ApprovalEntry]:
        response = self._request("GET", "/approvals/pending")
        items = response.json()
        return [
            ApprovalEntry(
                approval_id=item.get("approval_id", ""),
                description=item.get("description", ""),
                approver=item.get("approver", ""),
                status=item.get("status", ""),
                created_at=item.get("created_at"),
                decided_at=item.get("decided_at"),
            )
            for item in items
        ]

    def approve(self, approval_id: str) -> dict[str, Any]:
        response = self._request("POST", f"/approvals/{approval_id}/approve")
        return response.json()

    def reject(self, approval_id: str, reason: str) -> dict[str, Any]:
        response = self._request("POST", f"/approvals/{approval_id}/reject", json={"reason": reason})
        return response.json()

    def health(self) -> dict[str, Any]:
        response = self._request("GET", "/health")
        return response.json()

    def request_approval(
        self,
        description: str,
        approver: str,
        callback: dict[str, Any],
        thread_id: str | None = None,
    ) -> ApprovalEntry:
        request_payload: dict[str, Any] = {
            "intent": "request-approval",
            "description": description,
            "approver": approver,
            "callback_payload": callback,
        }
        if thread_id is not None:
            request_payload["thread_id"] = thread_id

        result = self._send_request(body=request_payload, wait=True)
        payload = result.payload or {}

        return ApprovalEntry(
            approval_id=str(payload.get("approval_id", result.envelope_id)),
            description=str(payload.get("description", description)),
            approver=str(payload.get("approver", approver)),
            status=str(payload.get("status", "pending")),
            created_at=payload.get("created_at"),
            decided_at=payload.get("decided_at"),
        )
