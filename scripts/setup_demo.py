"""One-command demo setup (development only): tables, synthetic data, policies, three demo users.

    python -m scripts.setup_demo

Safe to re-run. Refuses to run when APP_ENV=production.
"""

from sqlalchemy import select

import src.models  # noqa: F401
from scripts.load_policies import main as load_policies_main
from scripts.seed_banking_data import seed_data as seed_banking
from scripts.seed_kyc_data import seed_data as seed_kyc
from src.core.config import get_settings
from src.core.security import hash_password
from src.db.session import Base, SessionLocal, engine
from src.models.user import User

DEMO_PASSWORD = "Demo-Pass-2026!"
DEMO_USERS = (("demo_viewer", "viewer"), ("demo_reviewer", "reviewer"), ("demo_admin", "admin"))


def main() -> int:
    if get_settings().app_env == "production":
        print("Refusing to create demo users when APP_ENV=production.")
        return 1
    Base.metadata.create_all(bind=engine)
    seed_kyc()
    seed_banking()
    load_policies_main()
    with SessionLocal() as db:
        for username, role in DEMO_USERS:
            if db.scalar(select(User).where(User.username == username)) is None:
                db.add(User(username=username, password_hash=hash_password(DEMO_PASSWORD), role=role, is_active=True))
        db.commit()
    print("\n=== Demo ready (synthetic data only) ===")
    for username, role in DEMO_USERS:
        print(f"  {role:9} {username:14} password: {DEMO_PASSWORD}")
    print("\nStart:  python -m uvicorn src.api.main:app\nOpen:   http://127.0.0.1:8000/dashboard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
