from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class AlertRule(str, Enum):
    LOGIN_BRUTE_FORCE = "LOGIN_BRUTE_FORCE"
    TOKEN_ABUSE = "TOKEN_ABUSE"
    REPEATED_FORBIDDEN = "REPEATED_FORBIDDEN"
    CROSS_DEPARTMENT_PROBING = "CROSS_DEPARTMENT_PROBING"
    ADMIN_FEATURE_PROBING = "ADMIN_FEATURE_PROBING"
    ACTIVITY_SPIKE = "ACTIVITY_SPIKE"


class AlertSeverity(str, Enum):
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class SecurityAlert(BaseModel):
    """Alert derived from audit_logs columns only: it never carries tokens or secrets."""

    rule: AlertRule
    severity: AlertSeverity
    user_id: int | None
    user_email: str | None
    ip_address: str | None
    event_count: int
    threshold: float
    window_minutes: int
    first_seen: datetime
    last_seen: datetime
    details: dict[str, object]
