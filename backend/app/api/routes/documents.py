from datetime import UTC, datetime
from typing import Annotated, Literal

from docx.opc.exceptions import PackageNotFoundError
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pypdf.errors import PdfReadError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.api.dependencies import require_permission
from app.db.session import get_db
from app.models import Department, Document, User
from app.models.user import UserRole
from app.schemas.document import CreateDocumentRequest, DocumentResponse
from app.schemas.pagination import Page
from app.services.document_service import (
    extract_text,
    paginate,
    remove_document_file,
    save_document_file,
)

router = APIRouter(prefix="/documents", tags=["Documents Management"])

ROLE_PERMISSIONS = {
    UserRole.EMPLOYEE: {"read", "search"},
    UserRole.ADMIN: {"read", "search", "create", "update", "delete"},
}

SortOrder = Literal["asc", "desc"]


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


SORTABLE_COLUMNS = {
    "id": Document.id,
    "title": Document.title,
    "created_at": Document.created_at,
    "updated_at": Document.updated_at,
}


def _apply_sort(query, sort_by: str, order: SortOrder):
    column = SORTABLE_COLUMNS.get(sort_by)
    if column is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid sort field. Allowed: {', '.join(SORTABLE_COLUMNS)}",
        )
    return query.order_by(column.desc() if order == "desc" else column.asc())


def _build_page(items, total: int, page: int, page_size: int, pages: int) -> Page:
    return Page[DocumentResponse](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
        has_next=page < pages,
        has_prev=page > 1,
    )


@router.get("/get_all", response_model=Page[DocumentResponse])
def get_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: str = Query("created_at"),
    order: SortOrder = Query("desc"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("read", ROLE_PERMISSIONS)),
):
    query = select(Document).options(selectinload(Document.department))
    if current_user.role == UserRole.EMPLOYEE:
        query = query.where(Document.department_id == current_user.department_id)

    query = _apply_sort(query, sort_by, order)
    items, total, pages = paginate(db, query, page, page_size)
    return _build_page(items, total, page, page_size, pages)


@router.post("/add", response_model=DocumentResponse, status_code=201)
def create_document(
    document_data: Annotated[CreateDocumentRequest, Form()],
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("create", ROLE_PERMISSIONS)),
):
    if db.get(Department, document_data.department_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department does not exist",
        )

    saved_file_path = save_document_file(file)

    try:
        content = extract_text(saved_file_path)
    except (OSError, ValueError, PdfReadError, PackageNotFoundError):
        remove_document_file(saved_file_path)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not read the uploaded document",
        ) from None

    document = Document(
        title=document_data.title,
        department_id=document_data.department_id,
        content=content,
        file_path=saved_file_path,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )

    try:
        db.add(document)
        db.commit()
        db.refresh(document)
    except SQLAlchemyError:
        db.rollback()
        remove_document_file(saved_file_path)

        raise HTTPException(
            status_code=500,
            detail="Could not create document record",
        ) from None

    return document


@router.put("/update/{document_id}", response_model=DocumentResponse)
def update_document(
    document_id: int,
    document_data: Annotated[CreateDocumentRequest, Form()],
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("update", ROLE_PERMISSIONS)),
):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Requested document does not exist",
        )

    old_file_path = document.file_path

    if db.get(Department, document_data.department_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department does not exist",
        )
    document.department_id = document_data.department_id
    document.title = document_data.title

    new_file_path = save_document_file(file)
    try:
        document.content = extract_text(new_file_path)
    except (OSError, ValueError, PdfReadError, PackageNotFoundError):
        remove_document_file(new_file_path)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not read the uploaded document",
        ) from None

    document.file_path = new_file_path
    document.updated_at = _utcnow()

    try:
        db.commit()
        db.refresh(document)
    except SQLAlchemyError:
        db.rollback()
        remove_document_file(new_file_path)

        raise HTTPException(
            status_code=500,
            detail="Could not update document record",
        ) from None

    if old_file_path != new_file_path:
        remove_document_file(old_file_path)

    return document


@router.delete("/delete/{document_id}")
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("delete", ROLE_PERMISSIONS)),
):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Requested document does not exist",
        )

    file_path = document.file_path
    db.delete(document)
    db.commit()
    remove_document_file(str(file_path))

    return {"detail": "Document deleted successfully"}


@router.get("/search_document", response_model=Page[DocumentResponse])
def search_documents(
    q: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: str = Query("created_at"),
    order: SortOrder = Query("desc"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("search", ROLE_PERMISSIONS)),
):
    query = (
        select(Document)
        .options(selectinload(Document.department))
        .where(Document.content.ilike(f"%{q}%"))
    )
    if current_user.role == UserRole.EMPLOYEE:
        query = query.where(Document.department_id == current_user.department_id)

    query = _apply_sort(query, sort_by, order)
    items, total, pages = paginate(db, query, page, page_size)
    return _build_page(items, total, page, page_size, pages)
