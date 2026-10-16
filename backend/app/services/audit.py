from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditLog, AuditResult, ResourceType
from app.models.user import User
from app.schemas.audit import AuditLogResponse
from app.schemas.pagination import Page


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


PERIODS = {"today", "last7", "last30", "month"}


def _period_start(period: str | None) -> datetime | None:
    now = datetime.now(UTC).replace(tzinfo=None)
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "last7":
        return now - timedelta(days=7)
    if period == "last30":
        return now - timedelta(days=30)
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return None


def _base_query(
    *,
    action: str | None,
    result: str | None,
    user_email: str | None,
    period: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> Select:
    query = select(AuditLog, User.email).outerjoin(User, AuditLog.user_id == User.id)
    if action:
        query = query.where(AuditLog.action == action)
    if result:
        query = query.where(AuditLog.result == result)
    if user_email and user_email.strip():
        query = query.where(User.email.ilike(f"%{user_email.strip()}%"))
    start = _period_start(period)
    if start is not None:
        query = query.where(AuditLog.timestamp >= start)
    if date_from is not None:
        query = query.where(AuditLog.timestamp >= date_from)
    if date_to is not None:
        query = query.where(AuditLog.timestamp <= date_to)
    return query


def list_audit_logs(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    action: str | None = None,
    result: str | None = None,
    user_email: str | None = None,
    period: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> Page[AuditLogResponse]:
    filters = {
        "action": action,
        "result": result,
        "user_email": user_email,
        "period": period,
        "date_from": date_from,
        "date_to": date_to,
    }
    count_query = _base_query(**filters).with_only_columns(func.count(AuditLog.id))
    total = db.scalar(count_query) or 0

    rows = db.execute(
        _base_query(**filters)
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    items = [
        AuditLogResponse(
            id=log.id,
            user_id=log.user_id,
            user_email=email,
            action=log.action,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            result=log.result,
            ip_address=log.ip_address,
            timestamp=log.timestamp,
        )
        for log, email in rows
    ]

    pages = (total + page_size - 1) // page_size if total else 0
    return Page[AuditLogResponse](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
        has_next=page < pages,
        has_prev=page > 1 and pages > 0,
    )


def export_audit_logs(
    db: Session,
    *,
    action: str | None = None,
    result: str | None = None,
    user_email: str | None = None,
    period: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[AuditLogResponse]:
    rows = db.execute(
        _base_query(
            action=action,
            result=result,
            user_email=user_email,
            period=period,
            date_from=date_from,
            date_to=date_to,
        ).order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
    ).all()
    return [
        AuditLogResponse(
            id=log.id,
            user_id=log.user_id,
            user_email=email,
            action=log.action,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            result=log.result,
            ip_address=log.ip_address,
            timestamp=log.timestamp,
        )
        for log, email in rows
    ]
