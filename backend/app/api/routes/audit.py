import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import require_permission
from app.db.session import get_db
from app.models.audit_log import AuditAction, AuditResult, ResourceType
from app.models.user import User, UserRole
from app.schemas.audit import AuditLogResponse
from app.schemas.pagination import Page
from app.services.audit import export_audit_logs, list_audit_logs, log_audit_event

ROLE_PERMISSIONS = {
    UserRole.EMPLOYEE: set(),
    UserRole.ADMIN: {"read"},
}

router = APIRouter(prefix="/audit", tags=["Audit & Traceability"])
admin_read = require_permission("read", ROLE_PERMISSIONS)

CSV_COLUMNS = [
    "timestamp",
    "user_email",
    "action",
    "resource_type",
    "resource_id",
    "result",
    "ip_address",
]


def _normalize(value: str | None, allowed: set[str]) -> str | None:
    if value is None or value == "" or value.upper() == "ALL":
        return None
    if value not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported filter value: {value}",
        )
    return value


def _filters(
    action: str | None,
    result: str | None,
    period: str | None,
    user_email: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> dict[str, object]:
    return {
        "action": _normalize(action, {item.value for item in AuditAction}),
        "result": _normalize(result, {item.value for item in AuditResult}),
        "period": _normalize(period, {"today", "last7", "last30", "month"}),
        "user_email": user_email,
        "date_from": date_from,
        "date_to": date_to,
    }


@router.get("", response_model=Page[AuditLogResponse])
def get_audit_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    action: str | None = Query(default=None),
    result: str | None = Query(default=None),
    period: str | None = Query(default=None),
    user_email: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    _admin: User = Depends(admin_read),
) -> Page[AuditLogResponse]:
    return list_audit_logs(
        db,
        page=page,
        page_size=page_size,
        **_filters(action, result, period, user_email, date_from, date_to),
    )


def _csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


@router.get("/export")
def export_audit_logs_csv(
    request: Request,
    action: str | None = Query(default=None),
    result: str | None = Query(default=None),
    period: str | None = Query(default=None),
    user_email: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    admin: User = Depends(admin_read),
) -> Response:
    logs = export_audit_logs(
        db,
        **_filters(action, result, period, user_email, date_from, date_to),
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for log in logs:
        writer.writerow(
            [
                log.timestamp.isoformat(),
                _csv_cell(log.user_email),
                _csv_cell(log.action),
                _csv_cell(log.resource_type),
                _csv_cell(log.resource_id),
                _csv_cell(log.result),
                _csv_cell(log.ip_address),
            ]
        )

    log_audit_event(
        db,
        user=admin,
        action=AuditAction.EXPORT,
        resource_type=ResourceType.SESSION,
        resource_id=None,
        result=AuditResult.SUCCESS,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'},
    )
