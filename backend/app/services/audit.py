from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditLog, AuditResult, ResourceType
from app.models.user import User


def log_audit_event(
    db: Session,
    *,
    user: User | None,
    action: AuditAction,
    resource_type: ResourceType,
    resource_id: int | None,
    result: AuditResult,
    ip_address: str | None = None,
) -> None:
    """Persist an audit event without storing credentials or tokens."""
    event = AuditLog(
        user_id=user.id if user is not None else None,
        action=action.value,
        resource_type=resource_type.value,
        resource_id=resource_id,
        result=result.value,
        timestamp=datetime.now(UTC).replace(tzinfo=None),
        ip_address=ip_address,
    )
    db.add(event)
    db.flush()
