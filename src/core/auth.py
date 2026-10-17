
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from src.core.security import decode_access_token
from src.db.session import get_db
from src.models.user import User


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")

VIEW_ROLES = {"viewer", "reviewer", "admin"}


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """Resolve the authenticated user from the database."""
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired authentication credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_access_token(token)
    except (jwt.PyJWTError, ValueError, TypeError):
        raise credentials_error from None

    subject = payload.get("sub")
    if (
        not isinstance(subject, str)
        or not subject.isascii()
        or not subject.isdecimal()
        or subject.startswith("0")
    ):
        raise credentials_error

    user_id = int(subject)
    if user_id <= 0:
        raise credentials_error

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise credentials_error

    if user.role not in VIEW_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account role does not permit application access.",
        )

    return user


def require_roles(*allowed_roles: str):
    """Build a dependency that enforces current database roles."""
    if not allowed_roles or not set(allowed_roles).issubset(VIEW_ROLES):
        raise ValueError("Invalid or empty role configuration.")

    def role_checker(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action.",
            )
        return current_user

    return role_checker
