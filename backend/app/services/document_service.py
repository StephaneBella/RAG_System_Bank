import json
import re
import unicodedata
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

_DEPARTMENT_UNSAFE_CHARS = '/\\:*?"<>|'


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


def _fold(text: str) -> str:
    """NFKD normalization then strip diacritics, lower‑case."""
    return (
        unicodedata.normalize("NFKD", text)
        .encode("ascii", "ignore")
        .decode()
        .lower()
    )


_VISIBILITY_ALIASES = {
    "public": "public",
    "publique": "public",
    "pub": "public",
    "private": "private",
    "prive": "private",
    "privee": "private",
    "priv": "private",
}


def normalize_visibility(raw: str) -> str | None:
    """Map a visibility token (any case/accent) to ``public`` or ``private``."""
    return _VISIBILITY_ALIASES.get(_fold(raw).strip())


def sanitize_department_token(name: str) -> str:
    token = _fold(name)
    token = re.sub(r"[\s\-]+", "_", token)
    token = "".join(
        "_" if ch in _DEPARTMENT_UNSAFE_CHARS or ord(ch) < 32 else ch for ch in token
    )
    return re.sub(r"_+", "_", token).strip("_")


def humanize_title(raw: str) -> str:
    """Turn a stored slug ("my_report-2024") into a readable title ("My Report 2024")."""
    separated = re.sub(r"[_\-]+", " ", raw)
    words = separated.split()
    return " ".join(word[:1].upper() + word[1:] for word in words)


def sanitize_title(title: str) -> str:
    safe = Path(title).name
    safe = safe.replace(" ", "_")
    safe = "".join(
        "_" if ch in _DEPARTMENT_UNSAFE_CHARS or ord(ch) < 32 else ch for ch in safe
    ).strip("_")
    return safe or "untitled"


def build_stored_name(visibility: str, department_token: str, title: str, extension: str) -> str:
    return f"{visibility}__{department_token}__{sanitize_title(title)}{extension.lower()}"


def _infer_legacy_department(base: str) -> str | None:
    """Map a legacy filename base (without visibility prefix) to a department token."""
    normalized = _fold(base)
    if any(k in normalized for k in ("credit", "risque", "risques")):
        return "credit_&_risques"
    if any(k in normalized for k in ("conformite", "lbc")):
        return "direction_conformite"
    if any(k in normalized for k in ("front_office", "gestionnaires")):
        return "front_office"
    if any(k in normalized for k in ("direction_generale", "strategie", "rapport_annuel")):
        return "direction_generale"
    return None


def parse_stored_name(file_name: str) -> StoredDocument | None:
    # Determine how many "__" separators are present
    parts = file_name.split("__", 2)
    if len(parts) == 3:
        # 3‑part canonical format: visibility__department__title.ext
        visibility_raw, dept_raw, base = parts
        visibility = normalize_visibility(visibility_raw)
        if visibility is None or not dept_raw:
            return None
        department_token = _fold(dept_raw)          # normalized token for matching
        title = humanize_title(Path(base).stem)
        extension = Path(base).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            return None
        return StoredDocument(
            id=0,
            file_name=file_name,
            visibility=visibility,
            department_token=department_token,
            title=title,
            extension=extension,
            file_path=(Path("data") / "documents" / file_name).as_posix(),
        )
    elif len(parts) == 2:
        # 2‑part legacy format: visibility__title.ext  (e.g. Prive__Credit_Risques.pdf)
        visibility_raw, base = parts
        visibility = normalize_visibility(visibility_raw)
        if visibility is None or not base:
            return None
        # Infer department from title keywords
        department_token = _infer_legacy_department(base)
        if department_token is None:
            return None
        title = humanize_title(Path(base).stem)
        extension = Path(base).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            return None
        return StoredDocument(
            id=0,
            file_name=file_name,
            visibility=visibility,
            department_token=department_token,
            title=title,
            extension=extension,
            file_path=(Path("data") / "documents" / file_name).as_posix(),
        )
    else:
        return None


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


def document_path(file_name: str) -> Path:
    """Absolute path of a stored document (reads DOCUMENTS_DIR at call time)."""
    return DOCUMENTS_DIR / file_name


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
    # Build a lookup of actual files keyed by lower‑cased name
    actual_files = {p.name.lower(): p.name for p in list_document_files()}

    # Keep only index entries whose file name exists (case‑insensitive) in the folder
    kept = []
    for entry in index["documents"]:
        lower_name = entry["file_name"].lower()
        actual = actual_files.get(lower_name)
        if actual is not None:
            # Update the stored name to the real casing if it differs
            if entry["file_name"] != actual:
                entry["file_name"] = actual
            kept.append(entry)
        # else: stale entry – drop it

    index["documents"] = kept

    # Add any folder files that are not already in the index
    for lower_name, actual_name in actual_files.items():
        if (
            not any(doc["file_name"].lower() == lower_name for doc in index["documents"])
            and parse_stored_name(actual_name) is not None
        ):
            index["documents"].append({"id": index["next_id"], "file_name": actual_name})
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
        try:
            created_at, updated_at = _file_times(path)
        except FileNotFoundError:
            continue
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
