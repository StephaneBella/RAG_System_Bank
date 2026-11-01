from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    department_id: int = Field(gt=0)
    file_path: str = Field(min_length=1, max_length=500)


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    department_id: int | None = Field(default=None, gt=0)
    file_path: str | None = Field(default=None, min_length=1, max_length=500)


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    department_id: int
    file_path: str
    created_at: datetime
    updated_at: datetime
