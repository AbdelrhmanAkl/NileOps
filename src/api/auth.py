
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.core.security import create_access_token, verify_password
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
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Authenticate an active user and issue a short-lived access token."""
    settings = get_settings()
    username = form_data.username.strip()

    user = db.scalar(
        select(User).where(User.username == username)
    )

    if user is None:
        # Perform a real password-hash verification for unknown usernames.
        verify_password(form_data.password, DUMMY_PASSWORD_HASH)
        raise invalid_credentials_error()

    if not verify_password(form_data.password, user.password_hash):
        raise invalid_credentials_error()

    if not user.is_active:
        raise invalid_credentials_error()

    access_token = create_access_token(
        subject=str(user.id),
        role=user.role,
    )

    return TokenResponse(
        access_token=access_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )
