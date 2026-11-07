"""One-command demo setup (development only): tables, synthetic data, policies, three demo users.

    python -m scripts.setup_demo

Safe to re-run. Refuses to run when APP_ENV=production.
"""

from sqlalchemy import select

import src.models  # noqa: F401
from scripts.load_policies import main as load_policies_main
from scripts.seed_banking_data import seed_data as seed_banking
from scripts.seed_core_banking import apply_profile, ensure_branches, seed_core_banking, seed_customer_finances
from scripts.seed_kyc_data import seed_data as seed_kyc
from src.api.portal import create_customer_profile
from src.core.config import get_settings
from src.core.security import hash_password
from src.db.session import Base, SessionLocal, engine
from src.models.banking import AccountOpeningRequest, Customer
from src.models.kyc import ApplicationStatus, DocumentStatus, KYCApplication, KYCDocument
from src.models.portal import CustomerKycLink
from src.models.user import User

DEMO_PASSWORD = "Demo-Pass-2026!"
DEMO_USERS = (("demo_viewer", "viewer"), ("demo_reviewer", "reviewer"), ("demo_admin", "admin"))
DEMO_CUSTOMER = ("demo_customer", "سارة أحمد حسن")


def link_demo_kyc(db, limit: int = 8) -> int:
    """Give some seeded customers an approved KYC so their account requests can be approved.

    Customers without it stay blocked, which shows the rule: no account before identity verification.
    """
    requests = db.scalars(
        select(AccountOpeningRequest).where(AccountOpeningRequest.status == "under_review").order_by(AccountOpeningRequest.id)
    ).all()
    linked_customers = set(db.scalars(select(CustomerKycLink.customer_id)).all())
    linked_apps = set(db.scalars(select(CustomerKycLink.application_id)).all())
    free_apps = [
        a for a in db.scalars(select(KYCApplication).order_by(KYCApplication.id)).all() if a.id not in linked_apps
    ]
    done = 0
    for request in requests:
        if done >= limit or not free_apps:
            break
        if request.customer_id in linked_customers:
            continue
        customer = db.get(Customer, request.customer_id)
        application = free_apps.pop(0)
        application.customer_name = customer.full_name_ar
        application.status = ApplicationStatus.APPROVED.value
        for document in db.scalars(select(KYCDocument).where(KYCDocument.application_id == application.id)).all():
            document.status = DocumentStatus.VALID.value
        db.add(CustomerKycLink(application_id=application.id, customer_id=customer.id))
        linked_customers.add(customer.id)
        done += 1
    return done


def main() -> int:
    if get_settings().app_env == "production":
        print("Refusing to create demo users when APP_ENV=production.")
        return 1
    Base.metadata.create_all(bind=engine)
    seed_kyc()
    seed_banking()
    with SessionLocal() as db:
        stats = seed_core_banking(db)
        db.commit()
    print(f"Core banking data: {stats}")
    load_policies_main()
    with SessionLocal() as db:
        for username, role in DEMO_USERS:
            if db.scalar(select(User).where(User.username == username)) is None:
                db.add(User(username=username, password_hash=hash_password(DEMO_PASSWORD), role=role, is_active=True))
        username, full_name = DEMO_CUSTOMER
        if db.scalar(select(User).where(User.username == username)) is None:
            user = User(username=username, password_hash=hash_password(DEMO_PASSWORD), role="customer", is_active=True)
            db.add(user)
            db.flush()
            profile = create_customer_profile(db, user, full_name)
            apply_profile(profile, 500, ensure_branches(db))
            profile.full_name_ar = full_name
            seed_customer_finances(db, profile, 500)
        link_demo_kyc(db)
        db.commit()
    print("\n=== Demo ready (synthetic data only) ===")
    for username, role in DEMO_USERS:
        print(f"  {role:9} {username:14} password: {DEMO_PASSWORD}")
    print(f"  {'customer':9} {DEMO_CUSTOMER[0]:14} password: {DEMO_PASSWORD}")
    print("\nStart:  python -m uvicorn src.api.main:app\nOpen:   http://127.0.0.1:8000/dashboard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
