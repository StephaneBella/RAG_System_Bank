from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.models.user import User, UserStatus


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == email))

    if user is None:
        return None

    if user.status != UserStatus.ACTIVE:
        return None

    if not verify_password(password, user.password_hash):
        return None

    return user
