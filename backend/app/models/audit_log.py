from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditAction(str, Enum):
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    DOCUMENT_VIEW = "DOCUMENT_VIEW"
    DOCUMENT_SEARCH = "DOCUMENT_SEARCH"
    DOCUMENT_CREATE = "DOCUMENT_CREATE"
    DOCUMENT_UPDATE = "DOCUMENT_UPDATE"
    DOCUMENT_DELETE = "DOCUMENT_DELETE"
    USER_CREATE = "USER_CREATE"
    USER_UPDATE = "USER_UPDATE"
    USER_STATUS_UPDATE = "USER_STATUS_UPDATE"
    EXPORT = "EXPORT"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    ACCESS_DENIED = "ACCESS_DENIED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    TOKEN_MISSING = "TOKEN_MISSING"


# For now we can focus only on login and document access audits
class ResourceType(str, Enum):
    DOCUMENT = "DOCUMENT"
    USER = "USER"
    SESSION = "SESSION"
    AUDIT_LOG = "AUDIT_LOG"
    DEPARTMENT = "DEPARTMENT"


class AuditResult(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class AuditReason(str, Enum):
    """Closed list of refusal reasons: details never hold free text."""

    MISSING_TOKEN = "MISSING_TOKEN"
    INVALID_TOKEN = "INVALID_TOKEN"
    EXPIRED_TOKEN = "EXPIRED_TOKEN"
    REVOKED_TOKEN = "REVOKED_TOKEN"
    INACTIVE_OR_UNKNOWN_USER = "INACTIVE_OR_UNKNOWN_USER"
    ROLE_FORBIDDEN = "ROLE_FORBIDDEN"
    DEPARTMENT_FORBIDDEN = "DEPARTMENT_FORBIDDEN"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )

    action: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    resource_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    resource_id: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    result: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        index=True,
    )

    ip_address: Mapped[str] = mapped_column(
        String(45),
        nullable=True,
    )

    # Department of the acting user when the event happened.
    user_department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id"),
        nullable=True,
    )

    # Department of the document concerned, only when the library resolves it exactly.
    resource_department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id"),
        nullable=True,
    )

    duration_ms: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    # Structured only: {"reason": AuditReason} and/or {"document_ids": [int, ...]}.
    details: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )
