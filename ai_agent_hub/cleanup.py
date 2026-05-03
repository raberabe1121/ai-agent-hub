"""processed ディレクトリの定期クリーンアップ"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def cleanup_processed(days: int = 7, dry_run: bool = False) -> int:
    """7日以上前のprocessedファイルを削除する"""
    processed_dir = Path(os.environ.get("AI_AGENT_HUB_PROCESSED_DIR", "./processed"))
    if not processed_dir.exists():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
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
        "--days",
        type=int,
        default=7,
        help="Delete files older than N days (default: 7)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without deleting",
    )
    args = parser.parse_args()

    deleted = cleanup_processed(days=args.days, dry_run=args.dry_run)
    action = "Would delete" if args.dry_run else "Deleted"
    print(f"{action} {deleted} files older than {args.days} days")


if __name__ == "__main__":
    main()
