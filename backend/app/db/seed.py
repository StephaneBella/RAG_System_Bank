import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.department import Department
from app.models.user import User, UserRole, UserStatus

logger = logging.getLogger(__name__)

DEPARTMENT_NAMES = [
    "Direction Générale",
    "Conformité & Risques",
    "Direction Juridique",
    "Finances & Comptabilité",
    "Ressources Humaines",
]

ADMIN_DEPARTMENT_NAME = "Direction Générale"


def seed_departments(db: Session) -> int:
    existing = set(db.scalars(select(Department.name)).all())
    created = 0
    for name in DEPARTMENT_NAMES:
        if name not in existing:
            db.add(Department(name=name))
            created += 1
    if created:
        db.flush()
    return created


def seed_admin(db: Session) -> bool:
    if not settings.seed_admin_password:
        logger.warning("SEED_ADMIN_PASSWORD is not set; skipping admin seed")
        return False

    email = settings.seed_admin_email.strip().lower()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        return False

    department = db.scalar(select(Department).where(Department.name == ADMIN_DEPARTMENT_NAME))
    if department is None:
        return False

    db.add(
        User(
            email=email,
            password_hash=hash_password(settings.seed_admin_password),
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
            department_id=department.id,
        )
    )
    return True


def seed_initial_data() -> None:
    if not settings.seed_on_startup:
        return

    try:
        with SessionLocal() as db:
            departments_created = seed_departments(db)
            admin_created = seed_admin(db)
            if departments_created or admin_created:
                db.commit()
                logger.info(
                    "Seeded %d department(s) and admin user (%s created)",
                    departments_created,
                    "new" if admin_created else "already present",
                )
    except Exception:
        logger.exception("Failed to seed initial data (are migrations applied?)")
