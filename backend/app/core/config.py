from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PositiveInt = Annotated[int, Field(ge=1)]
PositiveFloat = Annotated[float, Field(gt=0, allow_inf_nan=False)]
NonEmptyStr = Annotated[str, Field(min_length=1)]


def _filled(value: object) -> bool:
    """A setting is filled when it holds non-blank text; a secret is read, never shown."""
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return isinstance(value, str) and bool(value.strip())


def _check_internal_url(
    value: str, hosts: list[str], name: str, hosts_name: str, *, https: bool | None = None
) -> None:
    """An http(s) URL whose host is allowed; messages name fields, never values."""
    try:
        parts = urlsplit(value)
        port_ok = parts.port is None or parts.port > 0  # .port raises on an invalid port
    except ValueError:
        raise ValueError(f"{name} must be a valid http(s) URL") from None
    if parts.scheme not in ("http", "https") or not parts.hostname or not port_ok:
        raise ValueError(f"{name} must be an http(s) URL with a host")
    if parts.username is not None or parts.password is not None:
        raise ValueError(f"{name} must not contain credentials")
    if parts.hostname.lower() not in hosts:
        raise ValueError(f"{name} host is not in {hosts_name}")
    if https is not None and (parts.scheme == "https") != https:
        raise ValueError(f"{name} scheme must match qdrant_https")


class Settings(BaseSettings):
    app_name: str = "RAG for Bank Document Library API"
    app_env: str = "dev"

    database_url: str

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    cors_origins: list[str] = ["http://localhost:5173"]

    seed_on_startup: bool = False
    seed_admin_email: str = "admin@banque.fr"
    seed_admin_password: str = ""

    # Suspicious activity detection (GET /audit/alerts)
    alert_lookback_hours: Annotated[int, Field(ge=1, le=168)] = 24
    alert_login_failures_user_threshold: PositiveInt = 5
    alert_login_failures_ip_threshold: PositiveInt = 10
    alert_login_failures_window_minutes: PositiveInt = 15
    alert_token_failures_user_threshold: PositiveInt = 5
    alert_token_failures_ip_threshold: PositiveInt = 10
    alert_missing_token_ip_threshold: PositiveInt = 30
    alert_token_failures_window_minutes: PositiveInt = 15
    alert_forbidden_threshold: PositiveInt = 10
    alert_forbidden_window_minutes: PositiveInt = 30
    alert_cross_department_threshold: PositiveInt = 3
    alert_cross_department_window_minutes: PositiveInt = 30
    alert_admin_probing_threshold: PositiveInt = 3
    alert_admin_probing_window_minutes: PositiveInt = 60
    alert_spike_min_events: PositiveInt = 60
    alert_spike_factor: Annotated[float, Field(ge=1.0)] = 5.0
    alert_spike_window_minutes: PositiveInt = 10
    alert_spike_baseline_days: PositiveInt = 7
    alert_spike_min_active_windows: PositiveInt = 6
    alert_document_enumeration_threshold: PositiveInt = 4
    alert_document_enumeration_window_minutes: PositiveInt = 30
    alert_search_burst_threshold: PositiveInt = 30
    alert_search_burst_window_minutes: PositiveInt = 1

    # RAG agent (app/services/Agentic_RAG). Disabled by default: the API runs without the
    # "rag" dependency group. Secrets are SecretStr: never printed by repr() or logs.
    rag_enabled: bool = False
    llm_model: str | None = None
    groq_api_key: SecretStr | None = None
    qdrant_url: str | None = None
    qdrant_api_key: SecretStr | None = None
    qdrant_collection: str = Field(default="rag_system_bank", min_length=1)
    # Limits of one RAG request (llm_request.RequestLimits): provider calls per request,
    # characters per call, and the timeout of each provider call, in seconds.
    max_provider_calls: PositiveInt = 12
    max_prompt_chars: PositiveInt = 32000
    timeout_seconds: PositiveFloat = 30.0
    # LLM provider: Groq only (ChatGroq, the provider of the RAG agent), no other provider and
    # no fallback. Groq is an external API: using it needs the explicit consent of decision
    # H1, allow_external_llm=True, on top of groq_api_key.
    allow_external_llm: bool = False
    # Qdrant server (never embedded in production): allowed internal host, API key, scheme.
    # qdrant_api_key is the key of the API; qdrant_ingest_api_key the one of the ingestion.
    qdrant_internal_hosts: list[str] = ["127.0.0.1", "localhost", "qdrant"]
    qdrant_https: bool = False
    qdrant_timeout: PositiveInt = 10
    qdrant_ingest_api_key: SecretStr | None = None
    # Embedding model: a Hugging Face name or a local directory; offline by default.
    embedding_model: NonEmptyStr = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_cache_dir: NonEmptyStr | None = None
    embedding_device: NonEmptyStr | None = None  # None: detected by the library
    embedding_offline: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # A validation error must never print the input values (secrets included).
        hide_input_in_errors=True,
    )

    @field_validator("qdrant_internal_hosts")
    @classmethod
    def normalise_hosts(cls, hosts: list[str]) -> list[str]:
        normalised = [host.strip().lower() for host in hosts]
        if any(not host for host in normalised):
            raise ValueError("internal hosts must not be blank")
        return normalised

    @model_validator(mode="after")
    def check_spike_windows(self) -> Self:
        # The R5 baseline must split into whole windows (7 days / 10 min = 1008).
        if (self.alert_spike_baseline_days * 1440) % self.alert_spike_window_minutes:
            raise ValueError(
                "alert_spike_baseline_days * 1440 must be divisible by alert_spike_window_minutes"
            )
        return self

    @model_validator(mode="after")
    def check_rag_settings(self) -> Self:
        # Fail at startup, not at the first question; only field names are reported.
        if not self.rag_enabled:
            return self
        required = ("llm_model", "groq_api_key", "qdrant_url", "qdrant_api_key")
        missing = [name for name in required if not _filled(getattr(self, name))]
        if missing:
            raise ValueError(f"rag_enabled requires: {', '.join(missing)}")
        if not self.allow_external_llm:
            raise ValueError(
                "groq is an external provider: rag_enabled requires allow_external_llm"
            )
        _check_internal_url(
            self.qdrant_url,
            self.qdrant_internal_hosts,
            "qdrant_url",
            "qdrant_internal_hosts",
            https=self.qdrant_https,
        )
        return self


settings = Settings()
