
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from pwdlib import PasswordHash

from src.core.config import get_settings


password_hash = PasswordHash.recommended()

ALLOWED_ROLES = {"admin", "reviewer", "viewer"}


def hash_password(password: str) -> str:
    """Return a secure password hash suitable for database storage."""
    if not password:
        raise ValueError("Password must not be empty.")

    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against its stored hash."""
    if not password or not hashed_password:
        return False

    try:
        return password_hash.verify(password, hashed_password)
    except (ValueError, TypeError):
        return False


def create_access_token(
    subject: str,
    role: str,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a signed access token for an authenticated user."""
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("Token subject must be a non-empty string.")

    if role not in ALLOWED_ROLES:
        raise ValueError("Invalid token role.")

    settings = get_settings()
    max_lifetime = timedelta(
        minutes=settings.access_token_expire_minutes
    )

    if expires_delta is not None:
        if expires_delta <= timedelta(0):
            raise ValueError("Token lifetime must be positive.")

        if expires_delta > max_lifetime:
            raise ValueError(
                "Token lifetime exceeds the configured maximum."
            )

    now = datetime.now(timezone.utc)
    expires_at = now + (
        expires_delta if expires_delta is not None else max_lifetime
    )

    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "iat": now,
        "exp": expires_at,
    }

    return jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """Validate a JWT signature and expiry before returning its claims."""
    settings = get_settings()

    payload = jwt.decode(
        token,
        settings.jwt_secret_key.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
        options={"require": ["sub", "iat", "exp"]},
    )

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise jwt.InvalidTokenError("Invalid token subject.")

    return payload
