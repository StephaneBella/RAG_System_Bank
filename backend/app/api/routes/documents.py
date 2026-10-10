from typing import Annotated
from datetime import datetime

from fastapi import Depends,APIRouter,Form, UploadFile,File,HTTPException,status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.dependencies import require_permission
from app.db.session import get_db
from app.main import app
from app.models import *
from app.schemas.document import DocumentResponse, CreateDocumentRequest
from app.services.document_service import save_document_file, remove_document_file, extract_text

router = APIRouter(prefix="/documents", tags=["Documents Management"])

ROLE_PERMISSIONS = {
    "employee": { "create", "read","update","delete", "search"},
    "admin": {"read", "search"}
}
DOCUMENT_PATH = "../data/documents"

@app.get("/get_all", response_model=list[DocumentResponse])
def get_documents(
        db: Session = Depends(get_db),
        current_user: User = Depends(require_permission("read",ROLE_PERMISSIONS))
):
    query = select(Document).options(selectinload(Document.department))
    if current_user.role == "employee":
        query = query.where(Document.department_id == current_user.department_id)
    return db.scalars(query).all()



@app.post("/add", response_model=DocumentResponse, status_code=201)
def create_document(
    document_data  = Annotated[CreateDocumentRequest, Form()],
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("create",ROLE_PERMISSIONS))
):
    #checking if department exist
    department = select(Department).where(Department.id==document_data.department_id)
    if department is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    saved_file_path = save_document_file(file)

    document = Document(
        title = document_data.title,
        department_id = document_data.department_id,
        content= extract_text(saved_file_path),
        file_path = saved_file_path,
        created_at = datetime.now(),
        updated_at = datetime.now()
    )

    # trying to save now
    try:
        db.add(document)
        db.commit()
        db.refresh(document)
    except Exception:
        db.rollback()
        remove_document_file(saved_file_path)

        raise HTTPException(
            status_code=500,
            detail="Could not create document record"
        )

    return document

@app.put("/update/{document_id}", response_model=DocumentResponse)
def update_document(
    document_id: int,
    document_data=Annotated[CreateDocumentRequest, Form()],
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("update", ROLE_PERMISSIONS))
):

    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail= "requested document does not found"
        )

    if document_data.department_id is not None:

        department = select(Department).where(Department.id == document_data.department_id)
        if department is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )
        document.department_id = document_data.department_id
    if document_data.title is not None:
        document.title = document_data.title


    if file is not None:
        new_file_path = save_document_file(file)
        document.file_path = new_file_path
        new_content = extract_text(new_file_path)
        document.content = new_content


    document.updated_at = datetime.now()

    try:
        db.commit()
        db.refresh(document)
    except Exception:
        db.rollback()

        raise HTTPException(
            status_code=500,
            detail="Could not update document record"
        )

@app.delete("/delete/{document_id")
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("delete", ROLE_PERMISSIONS))
):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="requested document does not found"
        )

    db.delete(document)
    db.commit()

    return  {"detail": "Document deleted successfully"}



@app.get("/search_document", response_model=list[DocumentResponse])
def search_documents(
    q:str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("search",ROLE_PERMISSIONS))
):
    query = (select(Document)
             .options(selectinload(Document.department)))
    if current_user.role == "employee":
        query = query.where(Document.department_id == current_user.department_id and Document.content.ilike(f"%{q}%"))
    return db.scalars(query.where(Document.content.ilike(f"%{q}%"))).all()








