"""Consistent backup of the SQLite database (safe while the app is running).

Usage: python -m scripts.backup_db [target_dir]   (default: backups/)
Uploaded documents live in UPLOAD_DIR and must be backed up separately (e.g. rsync).
"""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from src.core.config import get_settings


def main() -> int:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        print("Not a SQLite database; use your database's own backup tool.")
        return 1
    source = Path(url.removeprefix("sqlite:///"))
    if not source.exists():
        print(f"Database file not found: {source}")
        return 1
    target_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "backups")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"nileops-{datetime.now():%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    print(f"Backup written: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
