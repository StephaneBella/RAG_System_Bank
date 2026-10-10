import uuid
from pathlib import Path
from typing import Annotated, Literal

from docx.opc.exceptions import PackageNotFoundError
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pypdf.errors import PdfReadError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_permission
from app.db.session import get_db
from app.models import Department, User
from app.models.user import UserRole
from app.schemas.document import CreateDocumentRequest, DocumentResponse
from app.schemas.pagination import Page
from app.services.document_service import (
    StoredDocument,
    archive_file,
    build_stored_name,
    document_file_times,
    extract_text,
    find_by_id,
    load_documents,
    parse_stored_name,
    remove_document_file,
    rename_document_file,
    sanitize_department_token,
    save_index,
    sync_index_with_folder,
    write_document_file,
)

router = APIRouter(prefix="/documents", tags=["Documents Management"])

ROLE_PERMISSIONS = {
    UserRole.EMPLOYEE: {"read", "search"},
    UserRole.ADMIN: {"read", "search", "create", "update", "delete"},
}

SortOrder = Literal["asc", "desc"]

SORTABLE_FIELDS = {"id", "title", "created_at", "updated_at"}


def _department_map(db: Session) -> dict[str, Department]:
    return {
        sanitize_department_token(department.name): department
        for department in db.scalars(select(Department)).all()
    }


def _to_response(doc: StoredDocument, departments: dict[str, Department]) -> DocumentResponse:
    department = departments.get(doc.department_token)
    return DocumentResponse(
        id=doc.id,
        title=doc.title,
        department_id=department.id if department else 0,
        department_name=department.name if department else doc.department_token,
        file_path=doc.file_path,
    )


def _apply_sort(items: list[StoredDocument], sort_by: str, order: SortOrder) -> list[StoredDocument]:
    if sort_by not in SORTABLE_FIELDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid sort field. Allowed: {', '.join(sorted(SORTABLE_FIELDS))}",
        )
    return sorted(
        items,
        key=lambda doc: getattr(doc, sort_by),
        reverse=order == "desc",
    )


def _slice(items: list[StoredDocument], page: int, page_size: int) -> tuple[list[StoredDocument], int, int]:
    total = len(items)
    pages = (total + page_size - 1) // page_size if total else 0
    start = (page - 1) * page_size
    return items[start : start + page_size], total, pages


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


def _scoped_documents(db: Session, current_user: User) -> list[StoredDocument]:
    documents = load_documents(sync_index_with_folder())
    if current_user.role == UserRole.EMPLOYEE:
        mine = sanitize_department_token(current_user.department.name)
        documents = [doc for doc in documents if doc.department_token == mine]
    return documents


def _name_taken(index: dict, stored_name: str, exclude_id: int | None = None) -> bool:
    return any(
        doc["file_name"] == stored_name and doc["id"] != exclude_id
        for doc in index["documents"]
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
    departments = _department_map(db)
    documents = _scoped_documents(db, current_user)

    documents = _apply_sort(documents, sort_by, order)
    page_items, total, pages = _slice(documents, page, page_size)
    items = [_to_response(doc, departments) for doc in page_items]
    return _build_page(items, total, page, page_size, pages)


@router.post("/add", response_model=DocumentResponse, status_code=201)
def create_document(
    title: Annotated[str, Form()],
    department_id: Annotated[int, Form()],
    visibility: Annotated[Literal["public", "private"], Form()],
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("create", ROLE_PERMISSIONS)),
):
    document_data = CreateDocumentRequest(
        title=title,
        department_id=department_id,
        visibility=visibility,
    )
    if db.get(Department, department_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department does not exist",
        )

    index = sync_index_with_folder()
    stored_name = _build_request_name(db, document_data, file)
    if _name_taken(index, stored_name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A document with this title already exists in this department",
        )

    try:
        write_document_file(file, stored_name)
    except (ValueError, FileExistsError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File extension not allowed",
        ) from None

    try:
        extract_text(stored_name)
    except (OSError, ValueError, PdfReadError, PackageNotFoundError):
        remove_document_file(stored_name)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not read the uploaded document",
        ) from None

    document_id = index["next_id"]
    index["next_id"] += 1
    index["documents"].append({"id": document_id, "file_name": stored_name})
    save_index(index)

    departments = _department_map(db)
    return _to_response(_parsed_document(stored_name, document_id), departments)


@router.put("/update/{document_id}", response_model=DocumentResponse)
def update_document(
    document_id: int,
    title: Annotated[str, Form()],
    department_id: Annotated[int, Form()],
    visibility: Annotated[Literal["public", "private"], Form()],
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("update", ROLE_PERMISSIONS)),
):
    document_data = CreateDocumentRequest(
        title=title,
        department_id=department_id,
        visibility=visibility,
    )
    index = sync_index_with_folder()
    entry = find_by_id(index, document_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Requested document does not exist",
        )

    if db.get(Department, department_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department does not exist",
        )

    new_stored_name = _build_request_name(db, document_data, file)
    if _name_taken(index, new_stored_name, exclude_id=document_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A document with this title already exists in this department",
        )

    extension = Path(file.filename or "").suffix.lower()
    temp_name = f".tmp-{uuid.uuid4().hex}{extension}"
    try:
        write_document_file(file, temp_name)
    except (ValueError, FileExistsError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File extension not allowed",
        ) from None

    try:
        extract_text(temp_name)
    except (OSError, ValueError, PdfReadError, PackageNotFoundError):
        remove_document_file(temp_name)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not read the uploaded document",
        ) from None

    old_file_name = entry["file_name"]
    archive_file(old_file_name)
    rename_document_file(temp_name, new_stored_name)

    entry["file_name"] = new_stored_name
    save_index(index)

    departments = _department_map(db)
    return _to_response(_parsed_document(new_stored_name, document_id), departments)


@router.delete("/delete/{document_id}")
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("delete", ROLE_PERMISSIONS)),
):
    index = sync_index_with_folder()
    entry = find_by_id(index, document_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Requested document does not exist",
        )

    remove_document_file(entry["file_name"])
    index["documents"] = [doc for doc in index["documents"] if doc["id"] != document_id]
    save_index(index)

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
    departments = _department_map(db)
    documents = _scoped_documents(db, current_user)

    needle = q.lower()
    matches = []
    for doc in documents:
        try:
            content = extract_text(doc.file_path).lower()
        except (OSError, ValueError, PdfReadError, PackageNotFoundError):
            continue
        if needle in content:
            matches.append(doc)

    matches = _apply_sort(matches, sort_by, order)
    page_items, total, pages = _slice(matches, page, page_size)
    items = [_to_response(doc, departments) for doc in page_items]
    return _build_page(items, total, page, page_size, pages)


def _build_request_name(
    db: Session,
    document_data: CreateDocumentRequest,
    file: UploadFile,
) -> str:
    department = db.get(Department, document_data.department_id)
    extension = Path(file.filename or "").suffix.lower()
    return build_stored_name(
        document_data.visibility,
        sanitize_department_token(department.name),
        document_data.title,
        extension,
    )


def _parsed_document(stored_name: str, document_id: int) -> StoredDocument:
    parsed = parse_stored_name(stored_name)
    parsed.id = document_id
    parsed.created_at, parsed.updated_at = document_file_times(stored_name)
    return parsed