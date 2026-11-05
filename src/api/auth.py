
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.auth import get_current_user
from src.core.config import get_settings
from src.core.security import create_access_token, verify_password
from src.core.throttle import login_throttle
from src.models.banking import BankingAuditEvent
from src.db.session import get_db
from src.models.user import User


router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])

DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$zbsp4ASeSVo1EiVteo/2mg"
    "$TJnop9eJ+aPpFRJ0PD24Y1WhXpyfC9uyDirOr4P+uPE"
)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


def invalid_credentials_error() -> HTTPException:
    """Return a generic authentication error without exposing account details."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password.",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/token", response_model=TokenResponse)
def login(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Authenticate an active user and issue a short-lived access token."""
    settings = get_settings()
    username = form_data.username.strip()
    address = request.client.host if request.client else "unknown"

    wait = login_throttle.retry_after(username, address)
    if wait:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again later.",
            headers={"Retry-After": str(wait)},
        )

    user = db.scalar(
        select(User).where(User.username == username)
    )

    if user is None:
        # Perform a real password-hash verification for unknown usernames.
        verify_password(form_data.password, DUMMY_PASSWORD_HASH)
        login_throttle.register_failure(username, address)
        raise invalid_credentials_error()

    if not verify_password(form_data.password, user.password_hash) or not user.is_active:
        if login_throttle.register_failure(username, address):
            # Only real accounts are named in the audit trail (never attacker-typed text).
            db.add(
                BankingAuditEvent(
                    entity_type="auth",
                    entity_id=user.id,
                    action="LOGIN_LOCKED",
                    actor_role="system",
                    actor_username=user.username,
                    details_ar="تم إيقاف محاولات الدخول مؤقتًا بعد محاولات فاشلة متكررة.",
                )
            )
            db.commit()
        raise invalid_credentials_error()

    login_throttle.register_success(username, address)

    access_token = create_access_token(
        subject=str(user.id),
        role=user.role,
    )

    return TokenResponse(
        access_token=access_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


class MeResponse(BaseModel):
    username: str
    role: str


@router.get("/me", response_model=MeResponse)
def me(current_user: Annotated[User, Depends(get_current_user)]) -> MeResponse:
    """Who am I? The role comes from the database, not from the token."""
    return MeResponse(username=current_user.username, role=current_user.role)
