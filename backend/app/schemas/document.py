from pydantic import BaseModel, ConfigDict, Field, AliasPath

class CreateDocumentRequest(BaseModel):
    title : str
    department_id : int

class DocumentResponse(BaseModel):
    id: int
    title: str
    department_id:int
    department_name: str = Field(
        validation_alias= AliasPath("department", "name")
    )
    file_path: str



    model_config = ConfigDict(from_attributes=True)
