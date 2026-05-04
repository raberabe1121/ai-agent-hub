"""processed ディレクトリの定期クリーンアップ"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def cleanup_processed(hours: int = 24, dry_run: bool = False) -> int:
    """指定した時間より前のprocessedファイルを削除する"""
    processed_dir = Path(os.environ.get("AI_AGENT_HUB_PROCESSED_DIR", "./processed"))
    if not processed_dir.exists():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    deleted = 0
    for file_path in processed_dir.glob("*.json"):
        mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            if not dry_run:
                file_path.unlink()
            deleted += 1
    return deleted


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Cleanup processed envelopes")
    parser.add_argument(
        "--hours",
        type=int,
        default=int(os.environ.get("AI_AGENT_HUB_RETENTION_HOURS", "24")),
        help="Delete files older than N hours (default: AI_AGENT_HUB_RETENTION_HOURS or 24)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without deleting",
    )
    args = parser.parse_args()

    deleted = cleanup_processed(hours=args.hours, dry_run=args.dry_run)
    action = "Would delete" if args.dry_run else "Deleted"
    print(f"{action} {deleted} files older than {args.hours} hours")


if __name__ == "__main__":
    main()
