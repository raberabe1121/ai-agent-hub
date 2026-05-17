"""CLI for interacting with AI Agent Hub API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib import error, parse, request

import click


DEFAULT_API_URL = "http://localhost:8080"
DEFAULT_MODEL = "gemma3:4b"

DEFAULT_INTENTS: list[tuple[str, str]] = [
    ("ping", "疎通確認"),
    ("echo", "テキストをそのまま返す"),
    ("summarize", "テキストを要約する"),
    ("llm-query", "LLMに質問する"),
    ("cli-skill", "CLIコマンドを実行する"),
    ("cli-pipeline", "複数CLIコマンドをパイプラインで実行する"),
    ("request-approval", "承認リクエストを送る"),
    ("approve", "承認する"),
    ("reject", "却下する"),
    ("payment", "USDC決済を実行する"),
    ("entropy-check", "エントロピーを計算する"),
    ("rag-index", "RAGにドキュメントを登録する"),
    ("rag-query", "RAGを検索して回答する"),
]


def _normalize_url(api_url: str) -> str:
    return api_url.rstrip("/")


def _api_call(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: int = 30,
    query: dict[str, Any] | None = None,
) -> Any:
    full_url = url
    if query:
        encoded = parse.urlencode({k: v for k, v in query.items() if v is not None})
        if encoded:
            full_url = f"{url}?{encoded}"

    data = None
    headers = {"accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"

    req = request.Request(full_url, data=data, method=method.upper(), headers=headers)

    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            if not body:
                return {}
            return json.loads(body)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        detail = body or str(exc)
        raise click.ClickException(f"API error ({exc.code}): {detail}") from exc
    except error.URLError as exc:
        raise click.ClickException(f"API接続に失敗しました: {exc.reason}") from exc


def _extract_reply_payload(reply: Any) -> Any:
    if isinstance(reply, dict) and "payload" in reply:
        return reply["payload"]
    return reply


def _api_call_with_fallback(method: str, primary_url: str, fallback_url: str, *, payload: dict[str, Any], timeout: int) -> Any:
    try:
        return _api_call(method, primary_url, payload=payload, timeout=timeout)
    except click.ClickException as exc:
        message = str(exc)
        if "API error (404)" not in message:
            raise
        return _api_call(method, fallback_url, payload=payload, timeout=timeout)


@click.group(help="AI Agent Hub CLI")
def main() -> None:
    """Entry point for hub command."""


@main.command()
@click.option("--intent", required=True, type=str, help="intentの名前（必須）")
@click.option("--text", type=str, default=None, help="payloadのtextフィールド")
@click.option("--model", type=str, default=DEFAULT_MODEL, show_default=True, help="LLMモデル名")
@click.option("--wait", "wait_seconds", type=int, default=30, show_default=True, help="返信を待つ秒数")
@click.option("--no-wait", is_flag=True, default=False, help="返信を待たずにenvelope_idだけ返す")
@click.option("--api-url", type=str, default=DEFAULT_API_URL, show_default=True, help="APIサーバーのURL")
def send(intent: str, text: str | None, model: str, wait_seconds: int, no_wait: bool, api_url: str) -> None:
    """Envelopeを送信して返信を待つ。"""

    payload: dict[str, Any] = {"intent": intent}
    if text is not None:
        payload["text"] = text
    if model:
        payload["model"] = model

    base = _normalize_url(api_url)
    created = _api_call("POST", f"{base}/envelopes", payload=payload, timeout=wait_seconds)
    envelope_id = created.get("envelope_id", "")

    click.echo(f"→ Envelope送信: {envelope_id}")
    if no_wait:
        return

    reply = _api_call(
        "GET",
        f"{base}/envelopes/{envelope_id}/reply",
        query={"timeout_sec": wait_seconds},
        timeout=wait_seconds + 2,
    )
    click.echo("← 返信受信:")
    click.echo(f"   {json.dumps(_extract_reply_payload(reply), ensure_ascii=False)}")


@main.command()
@click.option("--thread-id", type=str, default=None, help="スレッドIDでフィルタ")
@click.option("--limit", type=int, default=20, show_default=True, help="表示件数")
@click.option("--intent", "intent_name", type=str, default=None, help="intentでフィルタ")
@click.option("--api-url", type=str, default=DEFAULT_API_URL, show_default=True, help="APIサーバーのURL")
def logs(thread_id: str | None, limit: int, intent_name: str | None, api_url: str) -> None:
    """処理済みEnvelopeのログを表示。"""

    base = _normalize_url(api_url)
    query = {"thread_id": thread_id, "limit": limit, "intent": intent_name}
    data = _api_call("GET", f"{base}/logs", query=query)

    items = []
    if isinstance(data, dict):
        if isinstance(data.get("logs"), list):
            items = data["logs"]
        elif isinstance(data.get("items"), list):
            items = data["items"]
    elif isinstance(data, list):
        items = data

    if not isinstance(items, list) or not items:
        click.echo("ログはありません")
        return

    for item in items:
        timestamp = item.get("time") or item.get("created_at") or "N/A"
        intent = item.get("intent")
        if intent is None and isinstance(item.get("payload"), dict):
            intent = item["payload"].get("intent")
        sender = item.get("from") or item.get("sender") or "unknown"
        recipient = item.get("to") or item.get("recipient") or "unknown"
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        summary = payload.get("text") or payload.get("message") or json.dumps(payload, ensure_ascii=False)
        click.echo(f"{timestamp} | {(intent or '-'):<14} | {sender} → {recipient} | ✅ {summary}")


@main.command()
@click.option("--api-url", type=str, default=DEFAULT_API_URL, show_default=True, help="APIサーバーのURL")
def pending(api_url: str) -> None:
    """承認待ち一覧を表示。"""

    base = _normalize_url(api_url)
    items = _api_call("GET", f"{base}/approvals/pending")
    if not isinstance(items, list):
        raise click.ClickException("pending API response is invalid")

    click.echo(f"承認待ち: {len(items)}件")
    for item in items:
        envelope_id = item.get("envelope_id", "")
        short_id = envelope_id[:8]
        description = item.get("description", "(説明なし)")
        approver = item.get("approver", "-")
        click.echo(f"  [{short_id}] {description}")
        click.echo(f"             approver: {approver}")


@main.command()
@click.argument("approval_id", type=str)
@click.option("--api-url", type=str, default=DEFAULT_API_URL, show_default=True, help="APIサーバーのURL")
def approve(approval_id: str, api_url: str) -> None:
    """承認する。"""

    base = _normalize_url(api_url)
    _api_call("POST", f"{base}/approvals/{approval_id}/approve", payload={})
    click.echo(f"承認しました: {approval_id}")


@main.command()
@click.argument("approval_id", type=str)
@click.option("--reason", type=str, default="", help="却下理由")
@click.option("--api-url", type=str, default=DEFAULT_API_URL, show_default=True, help="APIサーバーのURL")
def reject(approval_id: str, reason: str, api_url: str) -> None:
    """却下する。"""

    base = _normalize_url(api_url)
    _api_call("POST", f"{base}/approvals/{approval_id}/reject", payload={"reason": reason})
    click.echo(f"却下しました: {approval_id}")


@main.command()
@click.option("--api-url", type=str, default=DEFAULT_API_URL, show_default=True, help="APIサーバーのURL")
def status(api_url: str) -> None:
    """サービス状態を確認する。"""

    base = _normalize_url(api_url)
    health = _api_call("GET", f"{base}/health")
    services = health.get("services", {}) if isinstance(health, dict) else {}

    queue_path = services.get("queue_dir", "-")
    processed_path = services.get("processed_dir", "-")
    queue_ok = bool(services.get("queue_dir_exists"))
    processed_ok = bool(services.get("processed_dir_exists"))

    click.echo("AI Agent Hub ステータス")
    click.echo(f"  API Server:   ✅ {base}")
    click.echo(f"  Queue Dir:    {'✅' if queue_ok else '❌'} {queue_path}")
    click.echo(f"  Processed:    {'✅' if processed_ok else '❌'} {processed_path}")


@main.command()
@click.option("--api-url", type=str, default=DEFAULT_API_URL, show_default=True, help="APIサーバーのURL")
def intents(api_url: str) -> None:
    """利用可能なintent一覧を表示。"""

    base = _normalize_url(api_url)
    intents_data: list[tuple[str, str]] = []

    try:
        data = _api_call("GET", f"{base}/intents")
        if isinstance(data, dict) and isinstance(data.get("intents"), list):
            intents_data = [(name, "") for name in data["intents"] if isinstance(name, str)]
        elif isinstance(data, list):
            intents_data = [(name, "") for name in data if isinstance(name, str)]
    except click.ClickException:
        intents_data = []

    if not intents_data:
        intents_data = DEFAULT_INTENTS

    click.echo("利用可能なIntent:")
    for name, description in intents_data:
        if description:
            click.echo(f"  {name:<17} {description}")
        else:
            click.echo(f"  {name}")


def _read_rag_index_file(file_path: str) -> str:
    suffix = Path(file_path).suffix.lower()

    if suffix in {".txt", ".md"}:
        return click.open_file(file_path, mode="r", encoding="utf-8").read()

    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        return "\n".join((page.extract_text() or "") for page in reader.pages).strip()

    if suffix == ".docx":
        from docx import Document

        doc = Document(file_path)
        return "\n".join(paragraph.text for paragraph in doc.paragraphs).strip()

    raise click.ClickException("未対応のファイル形式です。対応: .txt .md .pdf .docx")


@main.command("rag-index")
@click.option("--text", type=str, default=None, help="インデックスするテキスト")
@click.option("--file", "file_path", type=click.Path(exists=True), default=None, help="インデックスするファイル")
@click.option("--source", type=str, default=None, help="ソース名")
@click.option("--api-url", type=str, default=DEFAULT_API_URL, show_default=True, help="APIサーバーのURL")
def rag_index(text: str | None, file_path: str | None, source: str | None, api_url: str) -> None:
    if not text and not file_path:
        raise click.ClickException("--text または --file のどちらかが必要です")
    if text and file_path:
        raise click.ClickException("--text と --file は同時に指定できません")

    content = text
    if file_path:
        content = _read_rag_index_file(file_path)
        if not source:
            source = file_path

    payload = {"intent": "rag-index", "text": content, "source": source}
    base = _normalize_url(api_url)
    result = _api_call_with_fallback("POST", f"{base}/rag/index", f"{base}/envelopes/wait", payload=payload, timeout=60)
    click.echo(json.dumps(_extract_reply_payload(result), ensure_ascii=False))


@main.command("rag-query")
@click.option("--query", required=True, type=str, help="検索クエリ")
@click.option("--limit", type=int, default=5, show_default=True, help="検索件数")
@click.option("--no-llm", is_flag=True, default=False, help="LLMを使わず検索結果のみ返す")
@click.option("--max-distance", type=float, default=None, help="この距離以上のドキュメントを除外")
@click.option("--api-url", type=str, default=DEFAULT_API_URL, show_default=True, help="APIサーバーのURL")
def rag_query(query: str, limit: int, no_llm: bool, max_distance: float | None, api_url: str) -> None:
    payload = {"intent": "rag-query", "query": query, "limit": limit, "use_llm": not no_llm, "max_distance": max_distance}
    base = _normalize_url(api_url)
    result = _api_call_with_fallback("POST", f"{base}/rag/query", f"{base}/envelopes/wait", payload=payload, timeout=60)
    click.echo(json.dumps(_extract_reply_payload(result), ensure_ascii=False))


if __name__ == "__main__":
    main()
