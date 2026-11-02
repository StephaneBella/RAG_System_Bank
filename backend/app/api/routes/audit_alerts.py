from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import require_permission
from app.db.session import get_db
from app.models.user import User, UserRole
from app.services.audit_detection import detect_suspicious_activity

ROLE_PERMISSIONS = {
    UserRole.EMPLOYEE: set(),
    UserRole.ADMIN: {"read"},
}

router = APIRouter(prefix="/audit", tags=["Audit & Traceability"])
admin_read = require_permission("read", ROLE_PERMISSIONS)


class AuditAlertResponse(BaseModel):
    rule: str
    severity: str
    subject_type: str
    subject: str
    count: int
    window_minutes: int
    message: str


@router.get("/alerts", response_model=list[AuditAlertResponse])
def get_audit_alerts(
    db: Session = Depends(get_db),
    _admin: User = Depends(admin_read),
) -> list[AuditAlertResponse]:
    return [AuditAlertResponse(**asdict(alert)) for alert in detect_suspicious_activity(db)]
