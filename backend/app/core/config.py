from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchoring on this file's location makes the .env lookup independent
# of the working directory the app is started from.
BACKEND_DIR = Path(__file__).resolve().parents[2]

# Values that must never be accepted as real secrets.
PLACEHOLDER_SECRETS = frozenset(
    {
        "change-me",
        "change-me-generate-a-real-secret",
        "changeme",
        "secret",
        "todo",
        "REPLACE_WITH_YOUR_PASSWORD",
    }
)

MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """Application settings loaded from environment variables and the .env file."""

    APP_ENV: str = "dev"
    APP_NAME: str = "RAG Document Library API"
    DATABASE_URL: SecretStr
    JWT_SECRET_KEY: SecretStr
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_jwt_secret(cls, value: SecretStr) -> SecretStr:
        """Reject placeholder or weak signing keys before the app can start."""
        raw = value.get_secret_value()
        if raw.lower() in PLACEHOLDER_SECRETS or raw in PLACEHOLDER_SECRETS:
            raise ValueError(
                "JWT_SECRET_KEY is a placeholder. Generate a real one with "
                "`uv run python scripts/generate_secret.py`."
            )
        if len(raw) < MIN_SECRET_LENGTH:
            raise ValueError(
                f"JWT_SECRET_KEY must be at least {MIN_SECRET_LENGTH} characters long."
            )
        return value

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        """Reject unconfigured connection strings so a wrong password fails fast."""
        raw = value.get_secret_value()
        if "REPLACE_WITH_YOUR_PASSWORD" in raw or ":password@" in raw:
            raise ValueError(
                "DATABASE_URL still contains a placeholder password. "
                "Put the real credentials in backend/.env."
            )
        return value

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_staging_database_url(cls, value: SecretStr, info) -> SecretStr:
        """Staging must never connect to a developer machine's local database."""
        if info.data.get("APP_ENV") == "staging" and "localhost" in value.get_secret_value():
            raise ValueError("APP_ENV=staging must not use a localhost DATABASE_URL.")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
