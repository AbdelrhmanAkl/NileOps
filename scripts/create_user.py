
from getpass import getpass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.core.security import hash_password
from src.db.session import SessionLocal, engine
from src.models.user import ALLOWED_ROLES, User


MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128
MAX_USERNAME_LENGTH = 100


def create_user() -> None:
    """Create an application user through an interactive local command."""
    username = input("Username: ").strip()

    if not username or len(username) > MAX_USERNAME_LENGTH:
        raise ValueError(
            f"Username must contain between 1 and "
            f"{MAX_USERNAME_LENGTH} characters."
        )

    role = input("Role (admin/reviewer/viewer): ").strip().lower()

    if role not in ALLOWED_ROLES:
        raise ValueError("Invalid role.")

    password = getpass("Password (input hidden): ")
    confirmation = getpass("Confirm password: ")

    if password != confirmation:
        raise ValueError("Passwords do not match.")

    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must contain at least "
            f"{MIN_PASSWORD_LENGTH} characters."
        )

    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must not exceed {MAX_PASSWORD_LENGTH} characters."
        )

    # Create only the users table if it does not already exist.
    User.__table__.create(bind=engine, checkfirst=True)

    with SessionLocal() as session:
        existing_user = session.scalar(
            select(User).where(User.username == username)
        )

        if existing_user is not None:
            raise ValueError("Username already exists.")

        user = User(
            username=username,
            password_hash=hash_password(password),
            role=role,
            is_active=True,
        )

        session.add(user)

        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise ValueError(
                "User could not be created. "
                "The username may already exist."
            ) from None

    print(
        f"User '{username}' created successfully "
        f"with role '{role}'."
    )


if __name__ == "__main__":
    try:
        create_user()
    except (ValueError, KeyboardInterrupt) as exc:
        if isinstance(exc, KeyboardInterrupt):
            print("\nUser creation cancelled.")
        else:
            print(f"User creation failed: {exc}")

        raise SystemExit(1)
