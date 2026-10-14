import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.user import User, UserStatus
from app.models.user_invitation import UserInvitation
from app.schemas.invitation import InvitationCreate
from app.services.user_service import department_exists, email_exists, get_user

INVITATION_LIFETIME = timedelta(hours=24)


def create_invitation(
    db: Session,
    invitation: InvitationCreate,
) -> tuple[User, str, datetime]:
    email = str(invitation.email).strip().lower()

    if email_exists(db, email):
        raise ValueError("Email already exists")
    if not department_exists(db, invitation.department_id):
        raise ValueError("Department does not exist")

    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    expires_at = now + INVITATION_LIFETIME

    user = User(
        email=email,
        password_hash=hash_password(secrets.token_urlsafe(48)),
        role=invitation.role,
        status=UserStatus.INACTIVE,
        department_id=invitation.department_id,
    )
    invitation_record = UserInvitation(
        user_id=0,
        token_hash=sha256(token.encode("utf-8")).hexdigest(),
        created_at=now,
        expires_at=expires_at,
    )

    try:
        db.add(user)
        db.flush()
        invitation_record.user_id = user.id
        db.add(invitation_record)
        db.commit()
        db.refresh(user)
    except Exception:
        db.rollback()
        raise

    return user, token, expires_at


def accept_invitation(db: Session, token: str, password: str) -> User:
    token_hash = sha256(token.encode("utf-8")).hexdigest()
    invitation = db.scalar(
        select(UserInvitation)
        .where(UserInvitation.token_hash == token_hash)
        .with_for_update()
    )

    if invitation is None or invitation.used_at is not None:
        raise ValueError("Invalid or already used invitation")

    now = datetime.now(UTC)
    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    if expires_at <= now:
        raise ValueError("Invitation has expired")

    user = db.get(User, invitation.user_id)
    if user is None:
        raise ValueError("Invitation account no longer exists")

    try:
        user.password_hash = hash_password(password)
        user.status = UserStatus.ACTIVE
        invitation.used_at = now
        db.commit()
    except Exception:
        db.rollback()
        raise

    return get_user(db, user.id) or user
