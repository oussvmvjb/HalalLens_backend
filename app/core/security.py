from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from jose import JWTError, jwt

from app.core.config import get_settings

password_hash = PasswordHasher()
TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    try:
        return password_hash.verify(hashed_password, password)
    except (InvalidHashError, VerifyMismatchError):
        return False


def create_token(subject: UUID, token_type: TokenType) -> str:
    settings = get_settings()
    lifetime = (
        timedelta(minutes=settings.access_token_minutes)
        if token_type == "access"
        else timedelta(days=settings.refresh_token_days)
    )
    now = datetime.now(timezone.utc)
    claims: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": now,
        "exp": now + lifetime,
    }
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


__all__ = ["JWTError", "create_token", "decode_token", "hash_password", "verify_password"]
