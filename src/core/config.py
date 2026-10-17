
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "NileOps"
    app_env: Literal["development", "testing", "production"] = "development"
    app_debug: bool = False

    database_url: str = "sqlite:///./data/nileops.db"
    upload_dir: str = "storage"
    max_upload_size_mb: int = Field(default=10, ge=1, le=50)

    jwt_secret_key: SecretStr = Field(
        min_length=32,
        validation_alias="JWT_SECRET_KEY",
    )
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_expire_minutes: int = Field(
        default=15,
        ge=1,
        le=60,
    )

    @property
    def resolved_upload_dir(self) -> Path:
        """Return the upload directory resolved against the project root."""
        path = Path(self.upload_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @model_validator(mode="after")
    def validate_security_settings(self):
        """Validate security-sensitive application settings."""
        secret = self.jwt_secret_key.get_secret_value()

        if len(secret) < 32:
            raise ValueError(
                "JWT_SECRET_KEY must contain at least 32 characters."
            )

        if secret == (
            "REPLACE_WITH_A_RANDOM_SECRET_OF_AT_LEAST_32_CHARACTERS"
        ):
            raise ValueError(
                "JWT_SECRET_KEY must be replaced with a random secret."
            )

        if self.app_env == "production" and self.app_debug:
            raise ValueError(
                "APP_DEBUG must be false in production."
            )

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
