from __future__ import annotations

import sys
import types
from click.testing import CliRunner

from ai_agent_hub import cli


runner = CliRunner()


def test_hub_status_exits_success(monkeypatch):
    def fake_api_call(method, url, **kwargs):
        assert method == "GET"
        assert url.endswith("/health")
        return {
            "status": "ok",
            "services": {
                "queue_dir": "/opt/ai-agent-hub/queue",
                "processed_dir": "/opt/ai-agent-hub/processed",
                "queue_dir_exists": True,
                "processed_dir_exists": True,
            },
        }

    monkeypatch.setattr(cli, "_api_call", fake_api_call)

    result = runner.invoke(cli.main, ["status"])

    assert result.exit_code == 0
    assert "AI Agent Hub ステータス" in result.output
    assert "API Server" in result.output


def test_hub_send_ping_prints_envelope_id(monkeypatch):
    def fake_api_call(method, url, **kwargs):
        if method == "POST":
            assert url.endswith("/envelopes")
            return {"envelope_id": "env-123", "status": "queued"}
        if method == "GET":
            return {"payload": {"pong": True}}
        raise AssertionError("unexpected call")

    monkeypatch.setattr(cli, "_api_call", fake_api_call)

    result = runner.invoke(cli.main, ["send", "--intent", "ping"])

    assert result.exit_code == 0
    assert "Envelope送信: env-123" in result.output


def test_hub_pending_lists_items(monkeypatch):
    def fake_api_call(method, url, **kwargs):
        assert method == "GET"
        assert url.endswith("/approvals/pending")
        return [
            {
                "envelope_id": "7698e25a-306f-4f10-bb61-0d9976746a75",
                "description": "海外出張経費 ¥150,000の承認申請",
                "approver": "https://company.local/@manager",
            }
        ]

    monkeypatch.setattr(cli, "_api_call", fake_api_call)

    result = runner.invoke(cli.main, ["pending"])

    assert result.exit_code == 0
    assert "承認待ち: 1件" in result.output
    assert "[7698e25a]" in result.output


def test_hub_intents_shows_intent_list(monkeypatch):
    def fake_api_call(method, url, **kwargs):
        assert method == "GET"
        assert url.endswith("/intents")
        return {"intents": ["ping", "echo", "llm-query"]}

    monkeypatch.setattr(cli, "_api_call", fake_api_call)

    result = runner.invoke(cli.main, ["intents"])

    assert result.exit_code == 0
    assert "利用可能なIntent:" in result.output
    assert "ping" in result.output
    assert "llm-query" in result.output


def test_send_no_wait_skips_reply_poll(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_api_call(method, url, **kwargs):
        calls.append((method, url))
        if method == "POST":
            return {"envelope_id": "env-no-wait", "status": "queued"}
        raise AssertionError("GET should not be called when --no-wait is used")

    monkeypatch.setattr(cli, "_api_call", fake_api_call)

    result = runner.invoke(cli.main, ["send", "--intent", "echo", "--text", "hello", "--no-wait"])

    assert result.exit_code == 0
    assert calls == [("POST", "http://localhost:8080/envelopes")]
    assert "env-no-wait" in result.output


def test_logs_handles_null_fields(monkeypatch):
    def fake_api_call(method, url, **kwargs):
        assert method == "GET"
        assert url.endswith("/logs")
        return {
            "logs": [
                {
                    "id": "env-1",
                    "time": None,
                    "intent": None,
                    "from": None,
                    "to": None,
                    "type": None,
                    "payload": {"answer": "ok"},
                }
            ],
            "total": 1,
        }

    monkeypatch.setattr(cli, "_api_call", fake_api_call)

    result = runner.invoke(cli.main, ["logs"])

    assert result.exit_code == 0
    assert "N/A" in result.output
    assert "unknown → unknown" in result.output


def test_rag_index_calls_rag_endpoint(monkeypatch):
    captured = {}

    def fake_api_call(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["payload"] = kwargs.get("payload")
        return {"status": "indexed", "doc_id": 1, "source": "memo"}

    monkeypatch.setattr(cli, "_api_call", fake_api_call)

    result = runner.invoke(cli.main, ["rag-index", "--text", "hello", "--source", "memo"])

    assert result.exit_code == 0
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/rag/index")


def test_rag_query_calls_rag_endpoint(monkeypatch):
    captured = {}

    def fake_api_call(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["payload"] = kwargs.get("payload")
        return {"query": "hello", "sources": []}

    monkeypatch.setattr(cli, "_api_call", fake_api_call)

    result = runner.invoke(cli.main, ["rag-query", "--query", "hello", "--no-llm"])

    assert result.exit_code == 0
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/rag/query")
    assert captured["payload"]["use_llm"] is False


def test_rag_index_reads_pdf(monkeypatch, tmp_path):
    captured = {}

    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_text("dummy", encoding="utf-8")

    class FakePage:
        def extract_text(self):
            return "pdf content"

    class FakeReader:
        def __init__(self, _path):
            self.pages = [FakePage()]

    def fake_api_call(method, url, **kwargs):
        captured["payload"] = kwargs.get("payload")
        return {"status": "indexed", "doc_id": 1}

    monkeypatch.setattr(cli, "_api_call", fake_api_call)
    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=FakeReader))

    result = runner.invoke(cli.main, ["rag-index", "--file", str(pdf_path)])

    assert result.exit_code == 0
    assert captured["payload"]["text"] == "pdf content"


def test_rag_index_reads_docx(monkeypatch, tmp_path):
    captured = {}

    docx_path = tmp_path / "doc.docx"
    docx_path.write_text("dummy", encoding="utf-8")

    class FakeParagraph:
        def __init__(self, text):
            self.text = text

    class FakeDoc:
        def __init__(self, _path):
            self.paragraphs = [FakeParagraph("line1"), FakeParagraph("line2")]

    def fake_api_call(method, url, **kwargs):
        captured["payload"] = kwargs.get("payload")
        return {"status": "indexed", "doc_id": 1}

    monkeypatch.setattr(cli, "_api_call", fake_api_call)
    monkeypatch.setitem(sys.modules, "docx", types.SimpleNamespace(Document=FakeDoc))

    result = runner.invoke(cli.main, ["rag-index", "--file", str(docx_path)])

    assert result.exit_code == 0
    assert captured["payload"]["text"] == "line1\nline2"
