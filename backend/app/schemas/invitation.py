from pydantic import BaseModel, EmailStr, Field

from app.models.user import UserRole


class InvitationCreate(BaseModel):
    email: EmailStr
    role: UserRole = UserRole.EMPLOYEE
    department_id: int = Field(gt=0)


class InvitationCreated(BaseModel):
    user_id: int
    email: EmailStr
    invitation_token: str
    expires_at: str


class InvitationAccept(BaseModel):
    token: str = Field(min_length=32, max_length=256)
    password: str = Field(min_length=12, max_length=128)
