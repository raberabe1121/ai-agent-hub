"""CLI for interacting with AI Agent Hub API."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import error, parse, request

import click

from ai_agent_hub import Envelope
from ai_agent_hub.magi import MagiSystem
from ai_agent_hub.policy import PolicyEngine
from ai_agent_hub.rag import RAGStore


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


def _truncate_preview(text: str, max_len: int = 100) -> str:
    import re

    normalized = text
    normalized = re.sub(r"```[\s\S]*?```", " [コード例] ", normalized)
    normalized = normalized.replace("**", "")
    normalized = normalized.replace("###", "")
    normalized = normalized.replace("##", "")
    normalized = normalized.replace("`", "")
    normalized = " ".join(normalized.split())
    if len(normalized) <= max_len:
        return normalized
    return normalized[:max_len] + "..."


def _render_rag_query_human(payload: dict[str, Any], *, no_llm: bool, query: str) -> str:
    lines: list[str] = []
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []

    if no_llm:
        lines.append(f'📚 検索結果: "{query}"')
        lines.append("")
        for idx, source in enumerate(sources, start=1):
            if not isinstance(source, dict):
                continue
            source_name = source.get("source") or "(unknown source)"
            distance = source.get("distance")
            distance_label = f"{float(distance):.2f}" if isinstance(distance, (int, float)) else "N/A"
            content = source.get("content") if isinstance(source.get("content"), str) else ""
            lines.append(f"  [{idx}] {source_name}  distance: {distance_label}")
            lines.append(f"      {_truncate_preview(content)}")
            lines.append("")
        if not sources:
            lines.append("  （結果なし）")
        return "\n".join(lines).rstrip()

    answer = payload.get("answer") if isinstance(payload.get("answer"), str) else ""
    lines.append(f"💬 {query}")
    lines.append("")
    lines.append(answer or "（回答なし）")
    lines.append("")
    lines.append("📚 参照元:")
    if sources:
        for idx, source in enumerate(sources, start=1):
            if not isinstance(source, dict):
                continue
            source_name = source.get("source") or "(unknown source)"
            distance = source.get("distance")
            distance_label = f"{float(distance):.2f}" if isinstance(distance, (int, float)) else "N/A"
            lines.append(f"  [{idx}] {source_name}  distance: {distance_label}")
    else:
        lines.append("  （結果なし）")
    return "\n".join(lines)


def _api_call_with_fallback(method: str, primary_url: str, fallback_url: str, *, payload: dict[str, Any], timeout: int) -> Any:
    try:
        return _api_call(method, primary_url, payload=payload, timeout=timeout)
    except click.ClickException as exc:
        message = str(exc)
        if "API error (404)" not in message:
            raise
        return _api_call(method, fallback_url, payload=payload, timeout=timeout)


HANDOFF_PROMPT = """以下は今日の作業セッションの会話ログです。
これを元に、別のAIエンジニアが作業を引き継げるよう
Markdown形式でまとめてください。

含めること:
- 今日やったこと（完了事項）
- 未完了・途中の作業
- 重要なコンテキスト・決定事項
- 次にやるべきこと

会話ログ:
{logs}
"""


def _sqlite_path() -> str:
    return os.environ.get(
        "AI_AGENT_HUB_SQLITE_PATH",
        os.environ.get("AI_AGENT_HUB_DB_PATH", "./agent_hub.db"),
    )


def _generate_handoff(logs: str, model: str) -> str:
    from ai_agent_hub.agent_worker import _handle_llm_query

    env = Envelope.new(
        envelope_type="command",
        sender="https://user.local/@me",
        recipient="https://ai-agent.local/@worker",
        payload={"intent": "llm-query", "text": HANDOFF_PROMPT.format(logs=logs), "model": model},
    )
    response = _handle_llm_query(env)
    result = response.get("result") if isinstance(response, dict) else None
    if not isinstance(result, str) or not result.strip():
        detail = response.get("error", "LLMから回答がありません") if isinstance(response, dict) else "invalid response"
        raise click.ClickException(f"引き継ぎドキュメントの生成に失敗しました: {detail}")
    return result


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


@main.command("policy-check")
@click.option("--intent", required=True, type=str, help="評価するintent")
@click.option("--text", type=str, default="", help="payloadのtextフィールド")
def policy_check(intent: str, text: str) -> None:
    """ローカルのpolicy.yamlでポリシー評価を実行する。"""

    env = Envelope.new(
        envelope_type="email",
        sender="https://user.local/@me",
        recipient="https://ai-agent.local/@worker",
        payload={"intent": intent, "text": text},
    )
    result = PolicyEngine().evaluate(env)

    click.echo(f"intent: {intent}")
    click.echo(f"result: {result.action}")
    if result.reason:
        click.echo(f"reason: {result.reason}")


@main.command("magi-evaluate")
@click.option("--intent", required=True, type=str, help="評価するintent")
@click.option("--text", required=True, type=str, help="評価するpayloadのtextフィールド")
def magi_evaluate(intent: str, text: str) -> None:
    """マギシステムでアクションを合議評価する。"""

    result = MagiSystem().evaluate(intent, text)
    for vote in result.votes:
        icon = "🟢" if vote.decision == "ALLOW" else "🔴"
        click.echo(f"{icon} {vote.persona:<10}: {vote.decision:<5} — {vote.reason}")
    click.echo("─────────────────────────────")
    click.echo(f"最終判断: {result.final}（{result.allow_count}-{result.deny_count}）")


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


def _format_count(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return "0"


@main.command("token-usage")
@click.option("--api-url", type=str, default=DEFAULT_API_URL, show_default=True, help="APIサーバーのURL")
def token_usage(api_url: str) -> None:
    """intent別のトークン使用量を表示。"""

    base = _normalize_url(api_url)
    data = _api_call("GET", f"{base}/token-usage")
    if not isinstance(data, dict):
        raise click.ClickException("token-usage API response is invalid")

    rows = data.get("by_intent") if isinstance(data.get("by_intent"), list) else []
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}

    click.echo("Intent           | 件数 | 入力    | 出力   | 合計")
    for row in rows:
        if not isinstance(row, dict):
            continue
        intent = str(row.get("intent") or "-")
        click.echo(
            f"{intent:<16} | {int(row.get('count') or 0):>4} | "
            f"{_format_count(row.get('prompt_tokens')):>7} | "
            f"{_format_count(row.get('completion_tokens')):>6} | "
            f"{_format_count(row.get('total_tokens')):>6}"
        )
    click.echo("─────────────────────────────────────────────────")
    click.echo(
        f"{'合計':<16} | {int(summary.get('count') or 0):>4} | "
        f"{_format_count(summary.get('prompt_tokens')):>7} | "
        f"{_format_count(summary.get('completion_tokens')):>6} | "
        f"{_format_count(summary.get('total_tokens')):>6}"
    )


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
@click.option("--date", "session_date", type=str, default=None, help="対象日 (YYYY-MM-DD)")
@click.option("--days", type=click.IntRange(min=1), default=None, help="今日を含む過去N日分")
@click.option("--model", type=str, default=DEFAULT_MODEL, show_default=True, help="LLMモデル名")
def handoff(session_date: str | None, days: int | None, model: str) -> None:
    """セッションログから引き継ぎMarkdownを生成する。"""
    if session_date is not None and days is not None:
        raise click.UsageError("--date と --days は同時に指定できません")

    if session_date is not None:
        try:
            end_date = datetime.strptime(session_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise click.BadParameter("YYYY-MM-DD形式で指定してください", param_hint="--date") from exc
        target_dates = [end_date]
    else:
        end_date = date.today()
        count = days or 1
        target_dates = [end_date - timedelta(days=offset) for offset in reversed(range(count))]

    store = RAGStore(_sqlite_path())
    documents: list[dict[str, Any]] = []
    for target_date in target_dates:
        documents.extend(store.get_documents_by_source(f"session/{target_date.isoformat()}"))

    if not documents:
        date_label = target_dates[0].isoformat() if len(target_dates) == 1 else f"{target_dates[0]}〜{target_dates[-1]}"
        raise click.ClickException(f"対象期間（{date_label}）のセッションログがありません")

    logs = "\n\n".join(
        f"[{item.get('source', '')}]\n{item.get('content', '')}" for item in documents
    )
    markdown = _generate_handoff(logs, model)
    output_path = Path(f"/tmp/handoff-{end_date.isoformat()}.md")
    output_path.write_text(markdown, encoding="utf-8")
    click.echo(markdown)
    click.echo(f"\n保存先: {output_path}")


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


def _is_meaningful_chunk(text: str, min_chars: int = 50) -> bool:
    normalized = " ".join(text.split())
    if len(normalized) < min_chars:
        return False
    stripped = normalized.replace("-", "").replace("_", "").replace("*", "").replace("=", "").replace("#", "")
    if not stripped.strip():
        return False
    return any(ch.isalnum() for ch in stripped)


def _split_markdown_sections(content: str, max_chunk_chars: int = 500) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_title = "document"
    current_lines: list[str] = []

    def _flush_section(title: str, lines: list[str]) -> None:
        body = "\n".join(lines).strip()
        if not body:
            return
        if len(body) <= max_chunk_chars:
            sections.append((title, body))
            return

        paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
        if not paragraphs:
            sections.append((title, body))
            return

        acc = ""
        for para in paragraphs:
            candidate = para if not acc else f"{acc}\n\n{para}"
            if len(candidate) <= max_chunk_chars:
                acc = candidate
                continue
            if acc:
                sections.append((title, acc))
            if len(para) <= max_chunk_chars:
                acc = para
            else:
                for i in range(0, len(para), max_chunk_chars):
                    part = para[i : i + max_chunk_chars].strip()
                    if part:
                        sections.append((title, part))
                acc = ""
        if acc:
            sections.append((title, acc))

    for line in content.splitlines():
        if line.startswith("## ") or line.startswith("### "):
            _flush_section(current_title, current_lines)
            current_title = line.lstrip("#").strip() or "section"
            current_lines = []
            continue
        current_lines.append(line)

    _flush_section(current_title, current_lines)
    return sections


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


def _read_rag_index_url(url: str, *, chunk_by_section: bool = False) -> str:
    import requests
    from bs4 import BeautifulSoup

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise click.ClickException(f"Webページの取得に失敗しました: {exc}") from exc

    soup = BeautifulSoup(response.content, "html.parser")
    for tag in soup(["script", "style", "nav"]):
        tag.decompose()

    content = soup.find("main") or soup.find("article") or soup.body or soup
    if chunk_by_section:
        for heading in content.find_all(["h2", "h3"]):
            level = "##" if heading.name == "h2" else "###"
            heading.replace_with(f"\n{level} {heading.get_text(' ', strip=True)}\n")

    return content.get_text("\n", strip=True)


@main.command("rag-index")
@click.option("--text", type=str, default=None, help="インデックスするテキスト")
@click.option("--file", "file_path", type=click.Path(exists=True), default=None, help="インデックスするファイル")
@click.option("--url", default=None, help="WebページのURLを指定してインデックス")
@click.option("--source", type=str, default=None, help="ソース名")
@click.option("--chunk-by-section", is_flag=True, default=False, help="Markdownの##見出しごとに分割してインデックス")
@click.option("--api-url", type=str, default=DEFAULT_API_URL, show_default=True, help="APIサーバーのURL")
def rag_index(
    text: str | None,
    file_path: str | None,
    url: str | None,
    source: str | None,
    chunk_by_section: bool,
    api_url: str,
) -> None:
    inputs = [value for value in (text, file_path, url) if value]
    if not inputs:
        raise click.ClickException("--text、--file、--url のいずれかが必要です")
    if len(inputs) > 1:
        raise click.ClickException("--text、--file、--url は同時に指定できません")

    content = text
    if file_path:
        content = _read_rag_index_file(file_path)
        if not source:
            source = file_path
    elif url:
        content = _read_rag_index_url(url, chunk_by_section=chunk_by_section)
        if not source:
            source = url

    base = _normalize_url(api_url)

    if chunk_by_section and (url or (file_path and Path(file_path).suffix.lower() == ".md")):
        effective_source = source or url or file_path
        chunks = _split_markdown_sections(content or "")
        if not chunks:
            raise click.ClickException("Markdownをセクション分割しましたが、インデックス可能な本文がありません")

        indexed: list[dict[str, Any]] = []
        filtered_chunks = [(title, body) for title, body in chunks if _is_meaningful_chunk(body)]
        title_totals: dict[str, int] = {}
        for title, _ in filtered_chunks:
            title_totals[title] = title_totals.get(title, 0) + 1

        title_counts: dict[str, int] = {}
        for title, body in filtered_chunks:
            title_counts[title] = title_counts.get(title, 0) + 1
            if title_totals.get(title, 0) > 1:
                chunk_source = f"{effective_source}#{title}-{title_counts[title]}"
            else:
                chunk_source = f"{effective_source}#{title}"
            payload = {
                "intent": "rag-index",
                "text": body,
                "source": chunk_source,
                "embedding_text": f"{title}\n{body}",
            }
            result = _api_call_with_fallback("POST", f"{base}/rag/index", f"{base}/envelopes/wait", payload=payload, timeout=60)
            indexed.append(_extract_reply_payload(result))

        if not indexed:
            raise click.ClickException("有効なチャンクがありません（短すぎるか記号のみ）")

        click.echo(json.dumps({"status": "indexed", "count": len(indexed), "items": indexed}, ensure_ascii=False))
        return

    payload = {"intent": "rag-index", "text": content, "source": source}
    result = _api_call_with_fallback("POST", f"{base}/rag/index", f"{base}/envelopes/wait", payload=payload, timeout=60)
    click.echo(json.dumps(_extract_reply_payload(result), ensure_ascii=False))


@main.command("rag-query")
@click.option("--query", required=True, type=str, help="検索クエリ")
@click.option("--limit", type=int, default=5, show_default=True, help="検索件数")
@click.option("--no-llm", is_flag=True, default=False, help="LLMを使わず検索結果のみ返す")
@click.option("--max-distance", type=float, default=None, help="この距離以上のドキュメントを除外")
@click.option("--json", "as_json", is_flag=True, default=False, help="JSON形式で出力する")
@click.option("--api-url", type=str, default=DEFAULT_API_URL, show_default=True, help="APIサーバーのURL")
def rag_query(query: str, limit: int, no_llm: bool, max_distance: float | None, as_json: bool, api_url: str) -> None:
    payload = {"intent": "rag-query", "query": query, "limit": limit, "use_llm": not no_llm, "max_distance": max_distance}
    base = _normalize_url(api_url)
    result = _api_call_with_fallback("POST", f"{base}/rag/query", f"{base}/envelopes/wait", payload=payload, timeout=60)
    reply_payload = _extract_reply_payload(result)
    if as_json:
        click.echo(json.dumps(reply_payload, ensure_ascii=False))
        return
    if not isinstance(reply_payload, dict):
        click.echo(json.dumps(reply_payload, ensure_ascii=False))
        return
    click.echo(_render_rag_query_human(reply_payload, no_llm=no_llm, query=query))


if __name__ == "__main__":
    main()
