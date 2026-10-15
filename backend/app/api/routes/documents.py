from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.audit_log import AuditAction, AuditResult, ResourceType
from app.models.department import Department
from app.models.document import Document
from app.models.user import User, UserRole
from app.schemas.documents import DocumentCreate, DocumentResponse, DocumentUpdate
from app.services.audit import log_audit_event

router = APIRouter(prefix="/documents", tags=["documents"])


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


def _require_admin(
    db: Session,
    user: User,
    request: Request,
    action: AuditAction,
) -> None:
    if user.role != UserRole.ADMIN:
        _audit(db, user, action, AuditResult.FAILURE, request)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required",
        )


def _validate_file_path(file_path: str) -> str:
    value = file_path.strip()
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)

    if (
        not value
        or "\x00" in value
        or "\\" in value
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in posix_path.parts
        or value in {".", ".."}
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="file_path must be a safe relative path",
        )

    return value


def _get_accessible_document(
    db: Session,
    document_id: int,
    user: User,
    request: Request,
) -> Document:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    if user.role != UserRole.ADMIN and document.department_id != user.department_id:
        _audit(
            db,
            user,
            AuditAction.DOCUMENT_VIEW,
            AuditResult.FAILURE,
            request,
            document.id,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access to this department's document is forbidden",
        )

    return document


@router.get("", response_model=list[DocumentResponse])
def list_documents(
    request: Request,
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Document]:
    statement = select(Document)

    if user.role != UserRole.ADMIN:
        statement = statement.where(Document.department_id == user.department_id)

    if search and search.strip():
        statement = statement.where(Document.title.ilike(f"%{search.strip()}%"))

    statement = statement.order_by(Document.id).offset(offset).limit(limit)
    documents = list(db.scalars(statement).all())

    if search and search.strip():
        _audit(
            db,
            user,
            AuditAction.DOCUMENT_SEARCH,
            AuditResult.SUCCESS,
            request,
        )
        db.commit()

    return documents


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Document:
    document = _get_accessible_document(db, document_id, user, request)
    _audit(
        db,
        user,
        AuditAction.DOCUMENT_VIEW,
        AuditResult.SUCCESS,
        request,
        document.id,
    )
    db.commit()
    return document


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
def create_document(
    payload: DocumentCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Document:
    _require_admin(db, user, request, AuditAction.DOCUMENT_CREATE)

    if db.get(Department, payload.department_id) is None:
        raise HTTPException(status_code=422, detail="Department not found")

    now = datetime.now(UTC).replace(tzinfo=None)
    document = Document(
        title=payload.title.strip(),
        department_id=payload.department_id,
        file_path=_validate_file_path(payload.file_path),
        created_at=now,
        updated_at=now,
    )
    if not document.title:
        raise HTTPException(status_code=422, detail="Title must not be blank")

    db.add(document)
    db.flush()
    _audit(
        db,
        user,
        AuditAction.DOCUMENT_CREATE,
        AuditResult.SUCCESS,
        request,
        document.id,
    )
    db.commit()
    db.refresh(document)
    return document


@router.put("/{document_id}", response_model=DocumentResponse)
def update_document(
    document_id: int,
    payload: DocumentUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Document:
    _require_admin(db, user, request, AuditAction.DOCUMENT_UPDATE)

    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    changes = payload.model_dump(exclude_unset=True)
    if "title" in changes:
        title = changes["title"].strip() if changes["title"] is not None else ""
        if not title:
            raise HTTPException(status_code=422, detail="Title must not be blank")
        document.title = title

    if "department_id" in changes:
        department_id = changes["department_id"]
        if department_id is None or db.get(Department, department_id) is None:
            raise HTTPException(status_code=422, detail="Department not found")
        document.department_id = department_id

    if "file_path" in changes:
        if changes["file_path"] is None:
            raise HTTPException(status_code=422, detail="file_path must not be null")
        document.file_path = _validate_file_path(changes["file_path"])

    document.updated_at = datetime.now(UTC).replace(tzinfo=None)
    _audit(
        db,
        user,
        AuditAction.DOCUMENT_UPDATE,
        AuditResult.SUCCESS,
        request,
        document.id,
    )
    db.commit()
    db.refresh(document)
    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    _require_admin(db, user, request, AuditAction.DOCUMENT_DELETE)

    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    _audit(
        db,
        user,
        AuditAction.DOCUMENT_DELETE,
        AuditResult.SUCCESS,
        request,
        document.id,
    )
    db.delete(document)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
