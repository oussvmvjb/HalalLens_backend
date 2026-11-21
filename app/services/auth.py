from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models import User
from app.repositories.users import user_by_email
from app.schemas.api import RegisterRequest


async def register_user(db: AsyncSession, payload: RegisterRequest) -> User:
    email = str(payload.email).lower()
    if await user_by_email(db, email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered")
    user = User(
        full_name=payload.full_name.strip(),
        email=email,
        hashed_password=hash_password(payload.password),
        preferred_language=payload.preferred_language.lower(),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email is already registered"
        ) from error
    await db.refresh(user)
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User:
    user = await user_by_email(db, email.lower())
    if user is None or not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive")
    return user
