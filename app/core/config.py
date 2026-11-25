from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "HalalLens API"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "sqlite+aiosqlite:///./halallens.db"
    redis_url: str = "redis://localhost:6379/0"
    deepl_api_key: str | None = None
    deepl_api_base_url: str = "https://api-free.deepl.com"
    jwt_secret_key: str = Field(
        default="local-development-only-change-this-secret",
        min_length=32,
    )
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = Field(default=30, gt=0)
    refresh_token_days: int = Field(default=30, gt=0)
    cors_origins: list[str] = [
        "http://localhost:56493",
        "http://127.0.0.1:56493",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8080",
    ]

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def normalize_database_url(self) -> "Settings":
        url = self.database_url.strip()
        if url.startswith("postgres://"):
            url = "postgresql+asyncpg://" + url[len("postgres://") :]
        elif url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://") :]
        self.database_url = url
        return self

    @model_validator(mode="after")
    def require_non_default_secret_outside_development(self) -> "Settings":
        if (
            self.environment.lower() not in {"development", "test"}
            and (
                self.jwt_secret_key == "local-development-only-change-this-secret"
                or self.jwt_secret_key.startswith("replace-with-")
            )
        ):
            raise ValueError("JWT_SECRET_KEY must be configured outside development")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
