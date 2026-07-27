from __future__ import annotations

from datetime import date

from click.testing import CliRunner

from ai_agent_hub import cli
import ai_agent_hub.agent_worker as agent_worker


def test_auto_save_context_saves_to_rag(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeStore:
        def __init__(self, db_path: str) -> None:
            captured["db_path"] = db_path

        def add_document(self, *, content: str, source: str) -> int:
            captured.update(content=content, source=source)
            return 1

    db_path = tmp_path / "context.db"
    monkeypatch.setenv("AI_AGENT_HUB_SQLITE_PATH", str(db_path))
    monkeypatch.setattr(agent_worker, "RAGStore", FakeStore)

    agent_worker._auto_save_context("llm-query", "質問", "回答")

    assert captured == {
        "db_path": str(db_path),
        "content": "Q: 質問\nA: 回答",
        "source": f"session/{date.today().isoformat()}",
    }


def test_auto_save_context_disabled_by_env(monkeypatch) -> None:
    class UnexpectedStore:
        def __init__(self, _db_path: str) -> None:
            raise AssertionError("RAGStore must not be created")

    monkeypatch.setenv("AI_AGENT_HUB_AUTO_CONTEXT", "false")
    monkeypatch.setattr(agent_worker, "RAGStore", UnexpectedStore)

    agent_worker._auto_save_context("llm-query", "質問", "回答")


def test_handoff_generates_markdown(monkeypatch) -> None:
    requested_sources: list[str] = []
    captured: dict[str, str] = {}

    class FakeStore:
        def __init__(self, _db_path: str) -> None:
            pass

        def get_documents_by_source(self, source_pattern: str) -> list[dict[str, str]]:
            requested_sources.append(source_pattern)
            return [{"source": source_pattern, "content": "Q: 実装は？\nA: 完了しました"}]

    def fake_generate(logs: str, model: str) -> str:
        captured.update(logs=logs, model=model)
        return "# 引き継ぎ\n\n- 実装完了"

    monkeypatch.setattr(cli, "RAGStore", FakeStore)
    monkeypatch.setattr(cli, "_generate_handoff", fake_generate)
    output_path = "/tmp/handoff-2026-07-05.md"

    result = CliRunner().invoke(cli.main, ["handoff", "--date", "2026-07-05"])

    assert result.exit_code == 0
    assert requested_sources == ["session/2026-07-05"]
    assert "Q: 実装は？" in captured["logs"]
    assert captured["model"] == cli.DEFAULT_MODEL
    assert "# 引き継ぎ" in result.output
    assert output_path in result.output
    with open(output_path, encoding="utf-8") as saved:
        assert saved.read() == "# 引き継ぎ\n\n- 実装完了"
