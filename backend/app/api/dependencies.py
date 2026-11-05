import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.audit_log import AuditAction, AuditReason, AuditResult, ResourceType
from app.models.revoked_token import RevokedToken
from app.models.user import User, UserStatus
from app.services.audit import log_audit_event

bearer_scheme = HTTPBearer(auto_error=False)


def _reject(
    db: Session,
    request: Request,
    *,
    status_code: int,
    detail: str,
    action: AuditAction,
    resource_type: ResourceType,
    reason: AuditReason,
    user: User | None = None,
    headers: dict[str, str] | None = None,
) -> HTTPException:
    """Persist the refusal before the error response; never receives the token or headers."""
    log_audit_event(
        db,
        user=user,
        action=action,
        resource_type=resource_type,
        resource_id=None,
        result=AuditResult.FAILURE,
        ip_address=request.client.host if request.client else None,
        reason=reason,
    )
    db.commit()
    return HTTPException(status_code=status_code, detail=detail, headers=headers)


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    def unauthorized(
        detail: str,
        reason: AuditReason,
        user: User | None = None,
        action: AuditAction = AuditAction.AUTHENTICATION_FAILED,
    ) -> HTTPException:
        return _reject(
            db,
            request,
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            action=action,
            resource_type=ResourceType.SESSION,
            reason=reason,
            user=user,
            headers={"WWW-Authenticate": "Bearer"},
        )

    if credentials is None or credentials.scheme.lower() != "bearer":
        # No bearer token at all (no header or another scheme): nothing to reject.
        raise unauthorized(
            "Not authenticated", AuditReason.MISSING_TOKEN, action=AuditAction.TOKEN_MISSING
        )

    try:
        payload = decode_access_token(credentials.credentials)
        subject = payload.get("sub")
        jti = payload.get("jti")
        if not subject or not jti:
            raise ValueError("Missing token claims")
        user_id = int(subject)
    except jwt.ExpiredSignatureError:
        # PyJWT checks the signature before expiry, so only authentic tokens land here.
        # The claims are still not trusted: the event stays anonymous.
        raise unauthorized(
            "Invalid or expired token",
            AuditReason.EXPIRED_TOKEN,
            action=AuditAction.SESSION_EXPIRED,
        ) from None
    except (jwt.InvalidTokenError, TypeError, ValueError):
        # The token failed verification: no claim is trusted, the event stays anonymous.
        raise unauthorized("Invalid or expired token", AuditReason.INVALID_TOKEN) from None

    user = db.scalar(select(User).options(selectinload(User.department)).where(User.id == user_id))

    if db.get(RevokedToken, jti) is not None:
        raise unauthorized("Token has been revoked", AuditReason.REVOKED_TOKEN, user)

    if user is None or user.status != UserStatus.ACTIVE:
        raise unauthorized(
            "User is not active or does not exist", AuditReason.INACTIVE_OR_UNKNOWN_USER, user
        )

    return user


def require_permission(
    permission: str,
    ROLE_PERMISSIONS: dict[str, set[str]],
    *,
    resource_type: ResourceType,
):
    def checker(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        allowed = ROLE_PERMISSIONS.get(current_user.role, set())

        if permission not in allowed:
            raise _reject(
                db,
                request,
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied",
                action=AuditAction.ACCESS_DENIED,
                resource_type=resource_type,
                reason=AuditReason.ROLE_FORBIDDEN,
                user=current_user,
            )
        return current_user

    return checker
