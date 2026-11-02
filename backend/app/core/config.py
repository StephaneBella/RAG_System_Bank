from typing import Annotated, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PositiveInt = Annotated[int, Field(ge=1)]


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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @model_validator(mode="after")
    def check_spike_windows(self) -> Self:
        # The R5 baseline must split into whole windows (7 days / 10 min = 1008).
        if (self.alert_spike_baseline_days * 1440) % self.alert_spike_window_minutes:
            raise ValueError(
                "alert_spike_baseline_days * 1440 must be divisible by alert_spike_window_minutes"
            )
        return self


settings = Settings()
