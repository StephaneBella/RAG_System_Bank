from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.dependencies import require_permission
from app.db.session import get_db
from app.models.audit_log import AuditAction, AuditResult, ResourceType
from app.models.user import User, UserRole
from app.schemas.invitation import InvitationCreate, InvitationCreated
from app.schemas.pagination import Page
from app.schemas.user import UserResponse, UserStatusUpdate, UserUpdate
from app.services.audit import log_audit_event
from app.services.invitation_service import create_invitation
from app.services.user_service import (
    USER_SORT_FIELDS,
    get_user,
    list_users,
    set_user_status,
    update_user,
)

ROLE_PERMISSIONS = {
    UserRole.EMPLOYEE: set(),
    UserRole.ADMIN: {"read", "search", "create", "update", "delete"},
}

router = APIRouter(prefix="/users", tags=["User Management"])
admin_read = require_permission("read", ROLE_PERMISSIONS)
admin_create = require_permission("create", ROLE_PERMISSIONS)
admin_update = require_permission("update", ROLE_PERMISSIONS)


def _audit(
    db: Session,
    user: User,
    action: AuditAction,
    result: AuditResult,
    request: Request,
    resource_id: int | None = None,
) -> None:
    log_audit_event(
        db,
        user=user,
        action=action,
        resource_type=ResourceType.USER,
        resource_id=resource_id,
        result=result,
        ip_address=request.client.host if request.client else None,
    )


@router.get("", response_model=Page[UserResponse])
def get_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    role: UserRole | None = None,
    department_id: int | None = Query(default=None, gt=0),
    search: str | None = None,
    sort_by: str = Query(default="id"),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
    _admin: User = Depends(admin_read),
) -> Page[UserResponse]:
    if sort_by not in USER_SORT_FIELDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported sort field",
        )
    try:
        return list_users(
            db,
            page=page,
            page_size=page_size,
            role=role,
            department_id=department_id,
            search=search,
            sort_by=sort_by,
            order=order,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/{user_id}", response_model=UserResponse)
def patch_user(
    user_id: int,
    changes: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_update),
) -> UserResponse:
    user = get_user(db, user_id)
    if user is None:
        _audit(db, admin, AuditAction.USER_UPDATE, AuditResult.FAILURE, request, user_id)
        db.commit()
        raise HTTPException(status_code=404, detail="User not found")

    try:
        updated = update_user(db, user, changes.model_dump(exclude_unset=True))
    except ValueError as exc:
        message = str(exc)
        code = 409 if message == "Email already exists" else 422
        _audit(db, admin, AuditAction.USER_UPDATE, AuditResult.FAILURE, request, user_id)
        db.commit()
        raise HTTPException(status_code=code, detail=message) from exc

    _audit(db, admin, AuditAction.USER_UPDATE, AuditResult.SUCCESS, request, updated.id)
    db.commit()

    return UserResponse(
        id=updated.id,
        email=updated.email,
        role=updated.role,
        status=updated.status,
        department_id=updated.department_id,
        department_name=updated.department.name,
    )


@router.post("/{user_id}/status", response_model=UserResponse)
def change_user_status(
    user_id: int,
    payload: UserStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_update),
) -> UserResponse:
    user = get_user(db, user_id)
    if user is None:
        _audit(db, admin, AuditAction.USER_STATUS_UPDATE, AuditResult.FAILURE, request, user_id)
        db.commit()
        raise HTTPException(status_code=404, detail="User not found")

    try:
        updated = set_user_status(db, user, payload.active)
    except ValueError as exc:
        _audit(db, admin, AuditAction.USER_STATUS_UPDATE, AuditResult.FAILURE, request, user_id)
        db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _audit(db, admin, AuditAction.USER_STATUS_UPDATE, AuditResult.SUCCESS, request, updated.id)
    db.commit()

    return UserResponse(
        id=updated.id,
        email=updated.email,
        role=updated.role,
        status=updated.status,
        department_id=updated.department_id,
        department_name=updated.department.name,
    )


@router.post(
    "/invitations",
    response_model=InvitationCreated,
    status_code=status.HTTP_201_CREATED,
)
def invite_user(
    payload: InvitationCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_create),
) -> InvitationCreated:
    try:
        user, token, expires_at = create_invitation(db, payload)
    except ValueError as exc:
        message = str(exc)
        code = 409 if message == "Email already exists" else 422
        _audit(db, admin, AuditAction.USER_CREATE, AuditResult.FAILURE, request)
        db.commit()
        raise HTTPException(status_code=code, detail=message) from exc

    _audit(db, admin, AuditAction.USER_CREATE, AuditResult.SUCCESS, request, user.id)
    db.commit()

    return InvitationCreated(
        user_id=user.id,
        email=user.email,
        invitation_token=token,
        expires_at=expires_at.isoformat(),
    )
