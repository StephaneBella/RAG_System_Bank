from pydantic import BaseModel, ConfigDict, EmailStr, Field, AliasPath

from app.models.user import UserRole, UserStatus


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CurrentUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    role: UserRole
    status: UserStatus
    department_id: int
    department_name: str = Field(validation_alias=AliasPath("department", "name"))
