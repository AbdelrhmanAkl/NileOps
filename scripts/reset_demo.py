"""Wipe ALL data and rebuild a clean synthetic demo (development/demo only).

    python -m scripts.reset_demo --yes

Drops every table, recreates the schema, then runs the demo setup (synthetic data,
policies, demo staff users and a demo customer). Refuses to run in production, and
requires --yes because it deletes everything, including uploaded-file records.
Uploaded files on disk are removed too (UPLOAD_DIR/documents).
"""

import shutil
import sys

import src.models  # noqa: F401
from scripts.setup_demo import main as setup_demo
from src.core.config import get_settings
from src.db.session import Base, engine


def main() -> int:
    settings = get_settings()
    if settings.app_env == "production":
        print("Refusing to wipe data when APP_ENV=production.")
        return 1
    if "--yes" not in sys.argv:
        print("This DELETES all data. Re-run with --yes to confirm.")
        return 1
    Base.metadata.drop_all(bind=engine)
    uploads = settings.resolved_upload_dir / "documents"
    if uploads.exists():
        shutil.rmtree(uploads)
    print("All data wiped.")
    return setup_demo()


if __name__ == "__main__":
    raise SystemExit(main())
