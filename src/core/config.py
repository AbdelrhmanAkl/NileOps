from functools import lru_cache
from pathlib import Path

from pydantic import Field
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
    app_env: str = "development"
    app_debug: bool = False

    database_url: str = "sqlite:///./data/nileops.db"
    upload_dir: str = "storage"
    max_upload_size_mb: int = Field(default=10, ge=1, le=50)

    @property
    def resolved_upload_dir(self) -> Path:
        """Return the upload directory resolved against the project root."""
        path = Path(self.upload_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
