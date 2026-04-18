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

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("AI_AGENT_HUB_URL") or "http://localhost:8080").rstrip("/")
        resolved_api_key = api_key or os.environ.get("AI_AGENT_HUB_API_KEY")
        default_headers: dict[str, str] = {}
        if resolved_api_key:
            default_headers["Authorization"] = f"Bearer {resolved_api_key}"
        if headers:
            default_headers.update(headers)
        self._client = httpx.Client(base_url=self.base_url, headers=default_headers or None)

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

    def query_llm(
        self,
        prompt: str,
        model: str = "gemma3:4b",
        timeout: int = 60,
    ) -> SendResult:
        """LLMに質問する専用メソッド"""
        return self.send(intent="llm-query", text=prompt, model=model, timeout=timeout)

    def run_cli_skill(
        self,
        skill: str,
        args: list[str],
        stdin: str | None = None,
        timeout: int = 30,
    ) -> SendResult:
        """CLIスキルを実行する専用メソッド"""
        import json

        payload: dict[str, Any] = {"skill": skill, "args": args}
        if stdin is not None:
            payload["stdin"] = stdin
        return self.send(
            intent="cli-skill",
            text=json.dumps(payload),
            timeout=timeout,
        )

    def run_cli_pipeline(
        self,
        steps: list[dict],
        stdin: str | None = None,
        timeout: int = 30,
    ) -> SendResult:
        """複数CLIコマンドをパイプラインで実行する専用メソッド"""
        import json

        payload: dict[str, Any] = {"steps": steps}
        if stdin is not None:
            payload["stdin"] = stdin
        return self.send(
            intent="cli-pipeline",
            text=json.dumps(payload),
            timeout=timeout,
        )

    def request_payment(
        self,
        amount: str,
        recipient: str,
        description: str = "",
        timeout: int = 30,
    ) -> SendResult:
        """USDC決済を実行する専用メソッド"""
        import json

        return self.send(
            intent="payment",
            text=json.dumps(
                {
                    "amount": amount,
                    "recipient": recipient,
                    "description": description,
                }
            ),
            timeout=timeout,
        )

    def check_entropy(
        self,
        thread_id: str,
        messages: list[str],
        threshold: float = 0.3,
        timeout: int = 30,
    ) -> SendResult:
        """エントロピーを計算する専用メソッド"""
        import json

        return self.send(
            intent="entropy-check",
            text=json.dumps(
                {
                    "thread_id": thread_id,
                    "messages": messages,
                    "threshold": threshold,
                }
            ),
            timeout=timeout,
        )

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

    def logs(
        self,
        limit: int = 20,
        offset: int = 0,
        intent: str | None = None,
        thread_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[LogEntry]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if intent is not None:
            params["intent"] = intent
        if thread_id is not None:
            params["thread_id"] = thread_id
        if since is not None:
            params["since"] = since
        if until is not None:
            params["until"] = until

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
        if result.status == "timeout":
            raise AgentHubTimeoutError("Timed out while waiting for approval request reply")

        payload = result.payload
        if not isinstance(payload, dict):
            raise AgentHubError("Approval request reply payload is missing or invalid")
        if "error" in payload:
            raise AgentHubError(f"Approval request failed: {payload['error']}")

        approval_id = payload.get("approval_id")
        if not isinstance(approval_id, str) or not approval_id:
            raise AgentHubError("Approval request reply does not contain approval_id")

        return ApprovalEntry(
            approval_id=approval_id,
            description=str(payload.get("description", description)),
            approver=str(payload.get("approver", approver)),
            status=str(payload.get("status", "pending")),
            created_at=payload.get("created_at"),
            decided_at=payload.get("decided_at"),
        )
