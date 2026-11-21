from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.session import get_db
from app.models import User

bearer_scheme = HTTPBearer(auto_error=False)
DatabaseSession = Annotated[AsyncSession, Depends(get_db)]


async def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DatabaseSession,
) -> User | None:
    if credentials is None:
        return None
    try:
        claims = decode_token(credentials.credentials)
        if claims.get("type") != "access":
            raise ValueError("Access token required")
        user_id = UUID(claims["sub"])
    except (JWTError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user")
    return user


async def get_current_user(user: Annotated[User | None, Depends(get_optional_user)]) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]
