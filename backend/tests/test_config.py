import pytest
from pydantic import ValidationError

from app.core.config import Settings

VALID_ENV = {
    "APP_ENV": "dev",
    "DATABASE_URL": "postgresql+psycopg://postgres:s3cure@localhost:5432/rag_project_dev",
    "JWT_SECRET_KEY": "a" * 64,
}


def build_settings(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> Settings:
    """Create Settings from monkeypatched env vars only, ignoring any real .env file."""
    for key, value in {**VALID_ENV, **overrides}.items():
        monkeypatch.setenv(key, value)
    for key in ("DATABASE_URL", "JWT_SECRET_KEY"):
        if key not in {**VALID_ENV, **overrides}:
            monkeypatch.delenv(key, raising=False)
    return Settings(_env_file=None)


def test_valid_config_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = build_settings(monkeypatch)
    assert settings.APP_ENV == "dev"
    assert settings.CORS_ORIGINS == ["http://localhost:5173"]


def test_missing_required_fields_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)
    assert "DATABASE_URL" in str(exc_info.value)
    assert "JWT_SECRET_KEY" in str(exc_info.value)


def test_placeholder_jwt_secret_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="placeholder"):
        build_settings(monkeypatch, JWT_SECRET_KEY="change-me-generate-a-real-secret")


def test_short_jwt_secret_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="at least 32"):
        build_settings(monkeypatch, JWT_SECRET_KEY="too-short")


def test_placeholder_database_password_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="placeholder password"):
        build_settings(
            monkeypatch,
            DATABASE_URL="postgresql+psycopg://postgres:REPLACE_WITH_YOUR_PASSWORD@localhost/db",
        )


def test_secrets_are_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = build_settings(monkeypatch)
    assert "a" * 64 not in repr(settings)
    assert "s3cure" not in repr(settings)
    assert "**********" in repr(settings)


def test_staging_rejects_localhost_database(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="localhost"):
        build_settings(monkeypatch, APP_ENV="staging")
