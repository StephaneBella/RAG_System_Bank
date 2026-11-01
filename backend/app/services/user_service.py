from math import ceil

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.department import Department
from app.models.user import User, UserRole, UserStatus
from app.schemas.pagination import Page
from app.schemas.user import UserResponse

USER_SORT_FIELDS = {
    "id": User.id,
    "email": User.email,
    "role": User.role,
    "status": User.status,
    "department_id": User.department_id,
}

DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 100


def list_users(
    db: Session,
    *,
    page: int = DEFAULT_PAGE,
    page_size: int = DEFAULT_PAGE_SIZE,
    role: UserRole | None = None,
    department_id: int | None = None,
    search: str | None = None,
    sort_by: str = "id",
    order: str = "asc",
) -> Page[UserResponse]:
    if page < 1:
        raise ValueError("page must be greater than or equal to 1")
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
    if sort_by not in USER_SORT_FIELDS:
        raise ValueError("Unsupported sort field")
    if order not in {"asc", "desc"}:
        raise ValueError("Unsupported sort order")

    filters = []
    if role is not None:
        filters.append(User.role == role)
    if department_id is not None:
        filters.append(User.department_id == department_id)
    if search and search.strip():
        term = f"%{search.strip()}%"
        filters.append(or_(User.email.ilike(term), Department.name.ilike(term)))

    total_statement = (
        select(func.count(User.id))
        .join(Department, User.department_id == Department.id)
        .where(*filters)
    )
    total = db.scalar(total_statement) or 0

    sort_column = USER_SORT_FIELDS[sort_by]
    sort_expression = sort_column.desc() if order == "desc" else sort_column.asc()

    statement = (
        select(User)
        .options(selectinload(User.department))
        .join(Department, User.department_id == Department.id)
        .where(*filters)
        .order_by(sort_expression, User.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    users = db.scalars(statement).all()

    items = [
        UserResponse(
            id=user.id,
            email=user.email,
            role=user.role,
            status=user.status,
            department_id=user.department_id,
            department_name=user.department.name,
        )
        for user in users
    ]

    pages = ceil(total / page_size) if total else 0
    return Page[UserResponse](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
        has_next=page < pages,
        has_prev=page > 1 and pages > 0,
    )


def get_department(db: Session, department_id: int) -> Department | None:
    return db.get(Department, department_id)


def get_user(db: Session, user_id: int) -> User | None:
    return db.scalar(select(User).options(selectinload(User.department)).where(User.id == user_id))


def email_exists(db: Session, email: str, *, exclude_user_id: int | None = None) -> bool:
    statement = select(User.id).where(func.lower(User.email) == email.lower())
    if exclude_user_id is not None:
        statement = statement.where(User.id != exclude_user_id)
    return db.scalar(statement) is not None


def department_exists(db: Session, department_id: int) -> bool:
    return db.scalar(select(Department.id).where(Department.id == department_id)) is not None


def _has_other_active_admin(db: Session, user: User) -> bool:
    count = db.scalar(
        select(func.count(User.id)).where(
            User.role == UserRole.ADMIN,
            User.status == UserStatus.ACTIVE,
            User.id != user.id,
        )
    )
    return bool(count)


def set_user_status(db: Session, user: User, active: bool) -> User:
    if (
        not active
        and user.role == UserRole.ADMIN
        and user.status == UserStatus.ACTIVE
        and not _has_other_active_admin(db, user)
    ):
        raise ValueError("Cannot deactivate the last active administrator")

    user.status = UserStatus.ACTIVE if active else UserStatus.INACTIVE
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_user(db, user.id) or user


def update_user(db: Session, user: User, changes: dict[str, object]) -> User:
    allowed_fields = {"email", "role", "department_id"}
    if not changes or set(changes) - allowed_fields:
        raise ValueError("No valid fields to update")
    if any(value is None for value in changes.values()):
        raise ValueError("Update values cannot be null")

    email = changes.get("email")
    if email is not None:
        normalized_email = str(email).strip().lower()
        if email_exists(db, normalized_email, exclude_user_id=user.id):
            raise ValueError("Email already exists")
        user.email = normalized_email

    role = changes.get("role")
    if role is not None:
        if not isinstance(role, UserRole):
            raise ValueError("Invalid role")
        if (
            role != UserRole.ADMIN
            and user.role == UserRole.ADMIN
            and user.status == UserStatus.ACTIVE
            and not _has_other_active_admin(db, user)
        ):
            raise ValueError("Cannot demote the last active administrator")
        user.role = role

    department_id = changes.get("department_id")
    if department_id is not None:
        if not isinstance(department_id, int) or not department_exists(db, department_id):
            raise ValueError("Department does not exist")
        user.department_id = department_id

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return get_user(db, user.id) or user
