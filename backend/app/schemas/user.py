from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserRole, UserStatus


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    role: UserRole
    status: UserStatus
    department_id: int
    department_name: str


class UserCreate(BaseModel):
    email: EmailStr
    role: UserRole = UserRole.EMPLOYEE
    department_id: int = Field(gt=0)


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    role: UserRole | None = None
    department_id: int | None = Field(default=None, gt=0)


class UserStatusUpdate(BaseModel):
    active: bool
