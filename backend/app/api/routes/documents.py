import uuid
from pathlib import Path
from typing import Annotated, Literal

from docx.opc.exceptions import PackageNotFoundError
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pypdf.errors import PdfReadError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models import Department, User
from app.models.audit_log import AuditAction, AuditResult, ResourceType
from app.models.user import UserRole
from app.schemas.document import CreateDocumentRequest, DocumentResponse
from app.schemas.pagination import Page
from app.services.audit import log_audit_event
from app.services.document_service import (
    StoredDocument,
    archive_file,
    build_stored_name,
    document_file_times,
    document_path,
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
MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _audit(
    db: Session,
    user: User,
    action: AuditAction,
    result: AuditResult,
    request: Request,
    resource_id: int | None = None,
) -> None:
    log_audit_event(
        db,
        user=user,
        action=action,
        resource_type=ResourceType.DOCUMENT,
        resource_id=resource_id,
        result=result,
        ip_address=_ip(request),
    )


def _require_permission(
    db: Session,
    user: User,
    permission: str,
    action: AuditAction,
    request: Request,
    resource_id: int | None = None,
) -> None:
    allowed = ROLE_PERMISSIONS.get(user.role, set())
    if permission not in allowed:
        _audit(db, user, action, AuditResult.FAILURE, request, resource_id)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to perform this action",
        )


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
        visibility=doc.visibility,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def _apply_sort(
    items: list[StoredDocument], sort_by: str, order: SortOrder
) -> list[StoredDocument]:
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


def _slice(
    items: list[StoredDocument], page: int, page_size: int
) -> tuple[list[StoredDocument], int, int]:
    total = len(items)
    pages = (total + page_size - 1) // page_size if total else 0
    start = (page - 1) * page_size
    return items[start : start + page_size], total, pages


def _build_page(
    items: list[DocumentResponse],
    total: int,
    page: int,
    page_size: int,
    pages: int,
) -> Page:
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
        if current_user.department is None:
            return []
        department_token = sanitize_department_token(current_user.department.name)
        documents = [
            doc
            for doc in documents
            if doc.department_token == department_token
        ]
    return documents


def _name_taken(index: dict, stored_name: str, exclude_id: int | None = None) -> bool:
    return any(
        doc["file_name"] == stored_name and doc["id"] != exclude_id for doc in index["documents"]
    )


def _parsed_document(stored_name: str, document_id: int) -> StoredDocument:
    parsed = parse_stored_name(stored_name)
    parsed.id = document_id
    parsed.created_at, parsed.updated_at = document_file_times(stored_name)
    return parsed


def _build_request_name(db: Session, document_data: CreateDocumentRequest, file: UploadFile) -> str:
    department = db.get(Department, document_data.department_id)
    if department is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department does not exist",
        )
    extension = Path(file.filename or "").suffix.lower()
    return build_stored_name(
        document_data.visibility,
        sanitize_department_token(department.name),
        document_data.title.strip(),
        extension,
    )


def _get_accessible_document(
    db: Session, document_id: int, user: User, request: Request
) -> StoredDocument:
    index = sync_index_with_folder()
    entry = find_by_id(index, document_id)
    if entry is None:
        _audit(db, user, AuditAction.DOCUMENT_VIEW, AuditResult.FAILURE, request, document_id)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    document = _parsed_document(entry["file_name"], document_id)
    if user.role != UserRole.ADMIN:
        same_department = (
            user.department is not None
            and document.department_token == sanitize_department_token(user.department.name)
        )
        if document.visibility != "public" and not same_department:
            _audit(db, user, AuditAction.DOCUMENT_VIEW, AuditResult.FAILURE, request, document_id)
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access to this document is forbidden",
            )
    return document


@router.get("/get_all", response_model=Page[DocumentResponse])
def get_documents(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: str = Query("created_at"),
    order: SortOrder = Query("desc"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_permission(db, current_user, "read", AuditAction.DOCUMENT_VIEW, request)
    departments = _department_map(db)
    documents = _scoped_documents(db, current_user)
    documents = _apply_sort(documents, sort_by, order)
    page_items, total, pages = _slice(documents, page, page_size)
    items = [_to_response(doc, departments) for doc in page_items]
    return _build_page(items, total, page, page_size, pages)


@router.get("/{document_id:int}", response_model=DocumentResponse)
def get_document(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_permission(db, current_user, "read", AuditAction.DOCUMENT_VIEW, request, document_id)
    document = _get_accessible_document(db, document_id, current_user, request)
    _audit(db, current_user, AuditAction.DOCUMENT_VIEW, AuditResult.SUCCESS, request, document.id)
    db.commit()
    return _to_response(document, _department_map(db))


@router.get("/{document_id:int}/content")
def get_document_content(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    _require_permission(db, current_user, "read", AuditAction.DOCUMENT_VIEW, request, document_id)
    document = _get_accessible_document(db, document_id, current_user, request)

    file_path = document_path(document.file_name)
    if not file_path.is_file():
        _audit(
            db, current_user, AuditAction.DOCUMENT_VIEW, AuditResult.FAILURE, request, document_id
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document file not found")

    _audit(db, current_user, AuditAction.DOCUMENT_VIEW, AuditResult.SUCCESS, request, document.id)
    db.commit()
    return FileResponse(
        path=file_path,
        media_type=MEDIA_TYPES.get(document.extension, "application/octet-stream"),
        filename=f"{document.title}{document.extension}",
        content_disposition_type="inline",
    )


@router.post("/add", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
def create_document(
    request: Request,
    title: Annotated[str, Form()],
    department_id: Annotated[int, Form()],
    visibility: Annotated[Literal["public", "private"], Form()],
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_permission(db, current_user, "create", AuditAction.DOCUMENT_CREATE, request)
    clean_title = title.strip()
    if not clean_title:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Title must not be blank"
        )
    if db.get(Department, department_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Department does not exist"
        )

    document_data = CreateDocumentRequest(
        title=clean_title, department_id=department_id, visibility=visibility
    )
    index = sync_index_with_folder()
    stored_name = _build_request_name(db, document_data, file)
    if _name_taken(index, stored_name):
        _audit(db, current_user, AuditAction.DOCUMENT_CREATE, AuditResult.FAILURE, request)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A document with this title already exists in this department",
        )

    try:
        write_document_file(file, stored_name)
        extract_text(stored_name)
    except (OSError, ValueError, FileExistsError, PdfReadError, PackageNotFoundError):
        remove_document_file(stored_name)
        _audit(db, current_user, AuditAction.DOCUMENT_CREATE, AuditResult.FAILURE, request)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not save or read the uploaded document",
        ) from None

    document_id = index["next_id"]
    index["next_id"] += 1
    index["documents"].append({"id": document_id, "file_name": stored_name})
    save_index(index)

    document = _parsed_document(stored_name, document_id)
    _audit(db, current_user, AuditAction.DOCUMENT_CREATE, AuditResult.SUCCESS, request, document_id)
    db.commit()
    return _to_response(document, _department_map(db))


@router.put("/update/{document_id}", response_model=DocumentResponse)
def update_document(
    document_id: int,
    request: Request,
    title: Annotated[str, Form()],
    department_id: Annotated[int, Form()],
    visibility: Annotated[Literal["public", "private"], Form()],
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_permission(
        db, current_user, "update", AuditAction.DOCUMENT_UPDATE, request, document_id
    )
    clean_title = title.strip()
    if not clean_title:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Title must not be blank"
        )
    if db.get(Department, department_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Department does not exist"
        )

    index = sync_index_with_folder()
    entry = find_by_id(index, document_id)
    if entry is None:
        _audit(
            db, current_user, AuditAction.DOCUMENT_UPDATE, AuditResult.FAILURE, request, document_id
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Requested document does not exist"
        )

    document_data = CreateDocumentRequest(
        title=clean_title, department_id=department_id, visibility=visibility
    )
    new_stored_name = _build_request_name(db, document_data, file)
    if _name_taken(index, new_stored_name, exclude_id=document_id):
        _audit(
            db, current_user, AuditAction.DOCUMENT_UPDATE, AuditResult.FAILURE, request, document_id
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A document with this title already exists in this department",
        )

    extension = Path(file.filename or "").suffix.lower()
    temp_name = f".tmp-{uuid.uuid4().hex}{extension}"
    try:
        write_document_file(file, temp_name)
        extract_text(temp_name)
    except (OSError, ValueError, FileExistsError, PdfReadError, PackageNotFoundError):
        remove_document_file(temp_name)
        _audit(
            db, current_user, AuditAction.DOCUMENT_UPDATE, AuditResult.FAILURE, request, document_id
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not save or read the uploaded document",
        ) from None

    old_file_name = entry["file_name"]
    try:
        archive_file(old_file_name)
        rename_document_file(temp_name, new_stored_name)
        entry["file_name"] = new_stored_name
        save_index(index)
    except OSError:
        remove_document_file(temp_name)
        _audit(
            db, current_user, AuditAction.DOCUMENT_UPDATE, AuditResult.FAILURE, request, document_id
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not replace the document file",
        ) from None

    document = _parsed_document(new_stored_name, document_id)
    _audit(db, current_user, AuditAction.DOCUMENT_UPDATE, AuditResult.SUCCESS, request, document_id)
    db.commit()
    return _to_response(document, _department_map(db))


@router.delete("/delete/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_permission(
        db, current_user, "delete", AuditAction.DOCUMENT_DELETE, request, document_id
    )
    index = sync_index_with_folder()
    entry = find_by_id(index, document_id)
    if entry is None:
        _audit(
            db, current_user, AuditAction.DOCUMENT_DELETE, AuditResult.FAILURE, request, document_id
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Requested document does not exist"
        )

    try:
        remove_document_file(entry["file_name"])
        index["documents"] = [doc for doc in index["documents"] if doc["id"] != document_id]
        save_index(index)
    except OSError:
        _audit(
            db, current_user, AuditAction.DOCUMENT_DELETE, AuditResult.FAILURE, request, document_id
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not delete the document file",
        ) from None

    _audit(db, current_user, AuditAction.DOCUMENT_DELETE, AuditResult.SUCCESS, request, document_id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/search_document", response_model=Page[DocumentResponse])
def search_documents(
    request: Request,
    q: str = Query(min_length=1, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: str = Query("created_at"),
    order: SortOrder = Query("desc"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_permission(db, current_user, "search", AuditAction.DOCUMENT_SEARCH, request)
    departments = _department_map(db)
    documents = _scoped_documents(db, current_user)

    needle = q.strip().lower()
    matches = []
    for doc in documents:
        try:
            content = extract_text(doc.file_path).lower()
        except (OSError, ValueError, PdfReadError, PackageNotFoundError):
            continue
        if needle in content or needle in doc.title.lower():
            matches.append(doc)

    matches = _apply_sort(matches, sort_by, order)
    page_items, total, pages = _slice(matches, page, page_size)
    items = [_to_response(doc, departments) for doc in page_items]
    _audit(db, current_user, AuditAction.DOCUMENT_SEARCH, AuditResult.SUCCESS, request)
    db.commit()
    return _build_page(items, total, page, page_size, pages)
