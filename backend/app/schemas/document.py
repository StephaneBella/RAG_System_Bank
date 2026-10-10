from typing import Literal

from pydantic import BaseModel, ConfigDict


class CreateDocumentRequest(BaseModel):
    title: str
    department_id: int
    visibility: Literal["public", "private"]


class DocumentResponse(BaseModel):
    id: int
    title: str
    department_id: int
    department_name: str
    file_path: str

    model_config = ConfigDict(from_attributes=True)
