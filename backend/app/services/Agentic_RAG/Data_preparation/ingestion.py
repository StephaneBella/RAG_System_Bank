from __future__ import annotations

"""Étape 1 : chargement des PDF avec LlamaIndex + métadonnées métier/sécurité.
 
Les métadonnées (doc_type, business_layer) sont déclarées statiquement dans
DOCUMENTS_METADATA, une entrée par fichier. Un PDF absent de ce registre est
refusé : aucun document n'est indexé sans étiquette de sécurité.
"""

import logging
from pathlib import Path

from llama_index.core import SimpleDirectoryReader
from llama_index.core.schema import Document

logger = logging.getLogger(__name__)


# Valeurs autorisées (à réutiliser coté RBAC / retriever)
PUBLIC, PRIVE = "public", "prive"
FRONT_OFFICE = "front_office"
CREDIT_RISQUES = "credit_risques"
CONFORMITE = "conformite_lbc_ft"
DIRECTION_GENERALE = "direction_generale"

# nom de fichier exact -> métadonnées
DOCUMENTS_METADATA: dict[str, dict] = {
    # --- Documents privés (internes, synthétiques) ---
    "Prive__Credit_Risques.pdf": {"doc_type": PRIVE, "business_layer": CREDIT_RISQUES},
    "Prive__Direction_conformite.pdf": {"doc_type": PRIVE, "business_layer": CONFORMITE},
    "Prive__Direction_Generale_Strategie.pdf": {
        "doc_type": PRIVE,
        "business_layer": DIRECTION_GENERALE,
    },
    "Prive__Front_Office_Gestionnaires_Comptes.pdf": {
        "doc_type": PRIVE,
        "business_layer": FRONT_OFFICE,
    },
    # --- Documents publics (réels) ---
    "Public__Credits_&_risques_Règlement_COBAC.pdf": {
        "doc_type": PUBLIC,
        "business_layer": CREDIT_RISQUES,
    },
    "Public__Direction_Conformite_Règlement_COBAC.pdf": {
        "doc_type": PUBLIC,
        "business_layer": CONFORMITE,
    },
    "Public__Direction_Generale_Strategie_Rapport_annuel_COBAC.pdf": {
        "doc_type": PUBLIC,
        "business_layer": DIRECTION_GENERALE,
    },
    "Public__Front_Office_Gestionnaires_Comptes_BICEC.pdf": {
        "doc_type": PUBLIC,
        "business_layer": FRONT_OFFICE,
    },
}


def normalize_metadata(doc: Document) -> Document:
    meta = doc.metadata
    file_name = meta.get("file_name", "unknown")
    page_label = meta.get("page_label")

    if file_name not in DOCUMENTS_METADATA:
        raise ValueError(
            f"Fichier non déclaré dans DOCUMENTS_METADATA : {file_name!r}. "
            "Ajoutez-le au registre avant de l'ingérer."
        )

    doc.metadata = {
        "source": file_name,
        "file_path": meta.get("file_path"),
        "page": int(page_label) if str(page_label).isdigit() else page_label,
        **DOCUMENTS_METADATA[file_name],
    }

    # Ces metadonnées ne doivent pas polluer le texte embeddé / lu par le LLM
    doc.excluded_embed_metadata_keys = list(doc.metadata.keys())
    doc.excluded_llm_metadata_keys = ["file_path"]
    return doc


def load_pdfs(path: str | Path, recursive: bool = True) -> list[Document]:
    """Charge un dossier de PDF (ou un PDF unique). Retourne un Document par page."""

    path = Path(path)
    if path.is_file():
        reader = SimpleDirectoryReader(input_files=[str(path)])
    elif path.is_dir():
        reader = SimpleDirectoryReader(
            input_dir=str(path), required_exts=[".pdf"], recursive=recursive
        )
    else:
        raise FileNotFoundError(path)

    documents = [normalize_metadata(d) for d in reader.load_data()]
    documents = [d for d in documents if d.text.strip()]  # pages vides ignorées
    logger.info("%d page(s) chargée(s) depuis %s", len(documents), path)

    return documents
