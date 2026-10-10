import uuid
from math import ceil
from pathlib import Path
from shutil import copyfileobj

from docx import Document as DocxDocument
from fastapi import HTTPException, UploadFile, status
from pypdf import PdfReader
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[3]

DOCUMENTS_DIR = PROJECT_ROOT / "data" / "documents"
ALLOWED_EXTENSIONS = {".pdf", ".docx"}


def save_document_file(file: UploadFile) -> str:
    file_name = file.filename or ""
    extension = Path(file_name).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="File Extension not allowed"
        )

    # creating the document directory if it does not exist
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)

    stored_name = f"{uuid.uuid4().hex}{extension}"
    destination = DOCUMENTS_DIR / stored_name

    try:
        with destination.open("wb") as buffer:
            copyfileobj(file.file, buffer)
    except OSError:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail="Could not save the uploaded file",
        )

    return (Path("data") / "documents" / stored_name).as_posix()


def remove_document_file(file_path: str):
    physical_file = PROJECT_ROOT / file_path
    physical_file.unlink(missing_ok=True)


def extract_text(file_path: str) -> str:

    path = Path(file_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    extension = path.suffix.lower()

    if extension == ".pdf":
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    if extension == ".docx":
        document = DocxDocument(str(path))
        return "\n".join(paragraph.text for paragraph in document.paragraphs)

    raise ValueError("Unsupported document format")


def paginate(
    db: Session,
    query: Select,
    page: int,
    page_size: int,
) -> tuple[list, int, int]:
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    pages = ceil(total / page_size) if total else 0
    return list(items), total, pages
