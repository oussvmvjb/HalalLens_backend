from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from jose import JWTError

from app.api.dependencies.auth import CurrentUser, DatabaseSession
from app.core.security import create_token, decode_token
from app.models import User
from app.schemas.api import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.services.auth import authenticate_user, register_user

router = APIRouter(prefix="/auth", tags=["Authentication"])


def tokens_for(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_token(user.id, "access"),
        refresh_token=create_token(user.id, "refresh"),
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db: DatabaseSession) -> User:
    return await register_user(db, payload)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: DatabaseSession) -> TokenResponse:
    user = await authenticate_user(db, str(payload.email), payload.password)
    return tokens_for(user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, db: DatabaseSession) -> TokenResponse:
    try:
        claims = decode_token(payload.refresh_token)
        if claims.get("type") != "refresh":
            raise ValueError("Refresh token required")
        user_id = UUID(claims["sub"])
    except (JWTError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token"
        ) from error
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user")
    return tokens_for(user)


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser) -> User:
    return user
