import json
from datetime import UTC, datetime
from pathlib import Path
from shutil import copyfileobj, move

from docx import Document as DocxDocument
from fastapi import UploadFile
from pypdf import PdfReader

PROJECT_ROOT = Path(__file__).resolve().parents[3]

DOCUMENTS_DIR = PROJECT_ROOT / "data" / "documents"
ARCHIVE_DIR = PROJECT_ROOT / "data" / "archive"

ALLOWED_EXTENSIONS = {".pdf", ".docx"}
ALLOWED_VISIBILITIES = {"public", "private"}
INDEX_NAME = ".documents.json"

_DEPARTMENT_UNSAFE_CHARS = "/\\:*?\"<>|"


class StoredDocument:
    def __init__(
        self,
        id: int,
        file_name: str,
        visibility: str,
        department_token: str,
        title: str,
        extension: str,
        file_path: str,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ):
        self.id = id
        self.file_name = file_name
        self.visibility = visibility
        self.department_token = department_token
        self.title = title
        self.extension = extension
        self.file_path = file_path
        self.created_at = created_at
        self.updated_at = updated_at


def sanitize_department_token(name: str) -> str:
    token = "".join(
        "_" if ch in _DEPARTMENT_UNSAFE_CHARS else ch for ch in name.lower()
    ).replace(" ", "_")
    return token.strip("_")


def sanitize_title(title: str) -> str:
    safe = Path(title).name
    safe = safe.replace(" ", "_")
    safe = "".join(
        "_" if ch in _DEPARTMENT_UNSAFE_CHARS or ord(ch) < 32 else ch for ch in safe
    ).strip("_")
    return safe or "untitled"


def build_stored_name(visibility: str, department_token: str, title: str, extension: str) -> str:
    return f"{visibility}__{department_token}__{sanitize_title(title)}{extension.lower()}"


def parse_stored_name(file_name: str) -> StoredDocument | None:
    parts = file_name.split("__", 2)
    if len(parts) != 3:
        return None

    visibility, department_token, base = parts
    if visibility not in ALLOWED_VISIBILITIES or not department_token:
        return None

    extension = Path(base).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        return None

    title = Path(base).stem
    return StoredDocument(
        id=0,
        file_name=file_name,
        visibility=visibility,
        department_token=department_token,
        title=title,
        extension=extension,
        file_path=(Path("data") / "documents" / file_name).as_posix(),
    )


def list_document_files() -> list[Path]:
    if not DOCUMENTS_DIR.exists():
        return []
    return [
        path
        for path in DOCUMENTS_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS
    ]


def _index_file() -> Path:
    return DOCUMENTS_DIR / INDEX_NAME


def load_index() -> dict:
    try:
        with _index_file().open("r", encoding="utf-8") as buffer:
            return json.load(buffer)
    except (OSError, json.JSONDecodeError):
        return {"next_id": 1, "documents": []}


def save_index(index: dict) -> None:
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    with _index_file().open("w", encoding="utf-8") as buffer:
        json.dump(index, buffer, indent=2)


def find_by_id(index: dict, document_id: int) -> dict | None:
    return next((doc for doc in index["documents"] if doc["id"] == document_id), None)


def sync_index_with_folder() -> dict:
    index = load_index()
    indexed = {doc["file_name"] for doc in index["documents"]}

    for path in sorted(list_document_files(), key=lambda p: p.name):
        if path.name in indexed:
            continue
        if parse_stored_name(path.name) is None:
            continue
        index["documents"].append({"id": index["next_id"], "file_name": path.name})
        index["next_id"] += 1

    save_index(index)
    return index


def write_document_file(file: UploadFile, stored_name: str) -> str:
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("File extension not allowed")

    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    destination = DOCUMENTS_DIR / stored_name
    if destination.exists():
        raise FileExistsError("A document with this name already exists")

    with destination.open("wb") as buffer:
        copyfileobj(file.file, buffer)

    return stored_name


def archive_file(file_name: str) -> None:
    source = DOCUMENTS_DIR / file_name
    archive_name = file_name
    if (ARCHIVE_DIR / archive_name).exists():
        stem, extension = Path(file_name).stem, Path(file_name).suffix
        archive_name = f"{stem}_{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}{extension}"
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    move(str(source), str(ARCHIVE_DIR / archive_name))


def remove_document_file(file_name: str) -> None:
    (DOCUMENTS_DIR / file_name).unlink(missing_ok=True)


def rename_document_file(source: str, destination: str) -> None:
    (DOCUMENTS_DIR / source).replace(DOCUMENTS_DIR / destination)


def document_file_times(file_name: str) -> tuple[datetime, datetime]:
    return _file_times(DOCUMENTS_DIR / file_name)


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


def _file_times(path: Path) -> tuple[datetime, datetime]:
    stat = path.stat()
    return (
        datetime.fromtimestamp(stat.st_ctime, UTC),
        datetime.fromtimestamp(stat.st_mtime, UTC),
    )


def load_documents(index: dict) -> list[StoredDocument]:
    documents: list[StoredDocument] = []
    for entry in index["documents"]:
        parsed = parse_stored_name(entry["file_name"])
        if parsed is None:
            continue
        path = DOCUMENTS_DIR / entry["file_name"]
        created_at, updated_at = _file_times(path)
        documents.append(
            StoredDocument(
                id=entry["id"],
                file_name=parsed.file_name,
                visibility=parsed.visibility,
                department_token=parsed.department_token,
                title=parsed.title,
                extension=parsed.extension,
                file_path=parsed.file_path,
                created_at=created_at,
                updated_at=updated_at,
            )
        )
    return documents