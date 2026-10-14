from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.department import Department


def list_departments(db: Session) -> list[Department]:
    statement = select(Department).order_by(Department.name.asc())
    return list(db.scalars(statement).all())
