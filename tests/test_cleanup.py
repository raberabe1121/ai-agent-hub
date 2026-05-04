from __future__ import annotations

from pathlib import Path

from ai_agent_hub.cleanup import cleanup_processed


def test_cleanup_processed_hours_zero_deletes_all_files(monkeypatch, tmp_path: Path) -> None:
    processed_dir = tmp_path / "processed"
    processed_dir.mkdir(exist_ok=True)
    for name in ("a.json", "b.json", "c.json"):
        (processed_dir / name).write_text("{}", encoding="utf-8")

    monkeypatch.setenv("AI_AGENT_HUB_PROCESSED_DIR", str(processed_dir))

    deleted = cleanup_processed(hours=0)

    assert deleted == 3
    assert list(processed_dir.glob("*.json")) == []


def test_cleanup_processed_dry_run_does_not_delete_files(monkeypatch, tmp_path: Path) -> None:
    processed_dir = tmp_path / "processed"
    processed_dir.mkdir(exist_ok=True)
    file_path = processed_dir / "a.json"
    file_path.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("AI_AGENT_HUB_PROCESSED_DIR", str(processed_dir))

    deleted = cleanup_processed(hours=0, dry_run=True)

    assert deleted == 1
    assert file_path.exists()


def test_cleanup_processed_large_hours_deletes_nothing(monkeypatch, tmp_path: Path) -> None:
    processed_dir = tmp_path / "processed"
    processed_dir.mkdir(exist_ok=True)
    file_path = processed_dir / "a.json"
    file_path.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("AI_AGENT_HUB_PROCESSED_DIR", str(processed_dir))

    deleted = cleanup_processed(hours=99999)

    assert deleted == 0
    assert file_path.exists()
