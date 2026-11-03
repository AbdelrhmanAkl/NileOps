"""Load policy files (knowledge/policies/*.md) into the database.

    python -m scripts.load_policies

Safe to re-run: unchanged files are skipped; changed content under an existing
version is reported as a conflict (bump `version:` in the file instead).
"""

from src.core.config import get_settings
from src.db.session import SessionLocal
import src.models  # noqa: F401
from src.db.session import Base, engine
from src.rag.chunking import read_policy_files
from src.rag.store import load_policies


def main() -> int:
    Base.metadata.create_all(bind=engine)
    parsed = read_policy_files(get_settings().resolved_policies_dir)
    with SessionLocal() as db:
        report = load_policies(db, parsed)
        db.commit()
    print(f"added: {report.added}\nunchanged: {report.unchanged}\nconflicts: {report.conflicts}")
    return 1 if report.conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
