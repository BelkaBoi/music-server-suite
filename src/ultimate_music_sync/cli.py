from __future__ import annotations

import argparse
from pathlib import Path

from .db import connect, migrate
from .legacy import import_dotify_snapshot

DEFAULT_HOME = Path.home() / ".ultimate-library"
DEFAULT_DOTIFY_QUEUE = Path.home() / ".dotify" / "liked-queue.json"
DEFAULT_DOTIFY_DB = Path.home() / ".dotify" / "liked-downloads.sqlite"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ums")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_HOME)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create or migrate the state database")
    legacy = sub.add_parser("import-dotify", help="Import a read-only Dotify state snapshot")
    legacy.add_argument("--queue", type=Path, default=DEFAULT_DOTIFY_QUEUE)
    legacy.add_argument("--database", type=Path, default=DEFAULT_DOTIFY_DB)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init":
        db_path = args.state_dir / "library.sqlite"
        db = connect(db_path)
        migrate(db)
        db.close()
        print(f"Initialized {db_path}")
        return 0
    if args.command == "import-dotify":
        db_path = args.state_dir / "library.sqlite"
        db = connect(db_path)
        migrate(db)
        report = import_dotify_snapshot(db, args.queue, args.database)
        db.close()
        statuses = ", ".join(f"{k}={v}" for k, v in sorted(report.queue_statuses.items()))
        print(
            f"Imported queue={report.queue_total} ({statuses}); "
            f"database={report.database_rows}; missing_paths={report.database_missing_paths}; "
            f"duplicate_path_groups={report.database_duplicate_paths}"
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
