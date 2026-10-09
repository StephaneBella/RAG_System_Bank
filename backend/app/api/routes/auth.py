from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import bearer_scheme, get_current_user
from app.core.security import create_access_token, decode_access_token
from app.db.session import get_db
from app.models.department import Department
from app.models.revoked_token import RevokedToken
from app.models.user import User
from app.schemas.auth import CurrentUserResponse, LoginRequest, TokenResponse
from app.services.auth import authenticate_user

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenResponse)
def login(
    credentials: LoginRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    user = authenticate_user(db, credentials.email, credentials.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    access_token = create_access_token(
        subject=str(user.id),
        role=user.role.value,
    )
    return TokenResponse(access_token=access_token)


@router.get("/me", response_model=CurrentUserResponse)
def get_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CurrentUserResponse:
    department = db.scalar(select(Department).where(Department.id == current_user.department_id))
    return CurrentUserResponse(
        id=current_user.id,
        email=current_user.email,
        role=current_user.role,
        status=current_user.status,
        department_id=current_user.department_id,
        department_name=department.name if department else None,
    )


@router.post("/logout")
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    payload = decode_access_token(credentials.credentials)
    jti = payload["jti"]
    if db.get(RevokedToken, jti) is None:
        expires_at = datetime.fromtimestamp(payload["exp"], UTC)
        db.add(RevokedToken(jti=jti, expires_at=expires_at))
        db.commit()

    return {"message": "Successfully logged out"}
