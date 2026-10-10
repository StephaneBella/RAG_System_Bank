import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.revoked_token import RevokedToken
from app.models.user import User, UserStatus

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = {
        "WWW-Authenticate": "Bearer",
    }
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers=unauthorized,
        )

    try:
        payload = decode_access_token(credentials.credentials)
        subject = payload.get("sub")
        jti = payload.get("jti")
        if not subject or not jti:
            raise ValueError("Missing token claims")
        user_id = int(subject)
    except (jwt.InvalidTokenError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers=unauthorized,
        ) from None

    if db.get(RevokedToken, jti) is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers=unauthorized,
        )

    user = db.scalar(select(User)
                    .options(selectinload(User.department))
                     .where(User.id == user_id))
    if user is None or user.status != UserStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is not active or does not exist",
            headers=unauthorized,
        )

    return user

def require_permission(
        permission: str,
        ROLE_PERMISSIONS: dict[str, set[str]]
):
    def checker( current_user: User= Depends(get_current_user)):
        allowed = ROLE_PERMISSIONS.get(current_user.role, set())

        if permission not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied"
            )
        return current_user
    return checker
