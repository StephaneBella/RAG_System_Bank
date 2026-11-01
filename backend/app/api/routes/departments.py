from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_permission
from app.db.session import get_db
from app.models.user import User, UserRole
from app.schemas.department import DepartmentResponse
from app.services.department_service import list_departments

ROLE_PERMISSIONS = {
    UserRole.EMPLOYEE: {"read", "search"},
    UserRole.ADMIN: {"read", "search", "create", "update", "delete"},
}

router = APIRouter(prefix="/departments", tags=["Department Management"])
admin_read = require_permission("read", ROLE_PERMISSIONS)


@router.get("", response_model=list[DepartmentResponse])
def get_departments(
    db: Session = Depends(get_db),
    _current_user: User = Depends(admin_read),
) -> list[DepartmentResponse]:
    return list_departments(db)
