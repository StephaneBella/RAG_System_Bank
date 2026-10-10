import uuid
from  pathlib import Path
from shutil import copyfileobj

from fastapi import HTTPException, UploadFile,status
from pypdf import PdfReader
from docx import Document as DocxDocument

PROJECT_ROOT = Path(__file__).resolve().parents[3]

DOCUMENTS_DIR = PROJECT_ROOT/"data"/"documents"
ALLOWED_EXTENSIONS = {".pdf",".doc",".docx"}

def save_document_file(file: UploadFile) -> str:
    file_name = file.filename or ""
    extension = Path(file_name).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File Extension not allowed"
        )

    # creating the document directory if it does not exist
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)

    stored_name = f"{uuid.uuid4().hex}{extension}"
    destination = DOCUMENTS_DIR/stored_name

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
   physical_file = PROJECT_ROOT/file_path
   physical_file.unlink(missing_ok=True)


def extract_text(file_path: str) -> str:

    file_path = Path(file_path)
    extension = file_path.suffix.lower()

    if extension == ".pdf":
        reader = PdfReader(str(file_path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    if extension == ".docx":
         document = DocxDocument(str(file_path))
         return "\n".join(paragraph.text for paragraph in document.paragraphs)

    raise ValueError("Unsupported document format")




