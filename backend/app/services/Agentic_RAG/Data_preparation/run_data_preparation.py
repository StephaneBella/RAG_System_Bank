"""Pipeline : ingestion -> chunking sémantique -> vectorisation -> storing."""

import logging
import sys
import warnings

from chunking import chunk_documents
from ingestion import load_pdfs
from llama_index.core.schema import MetadataMode
from storing import store_nodes
from vectorisation import embed_texts

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")


def warn(*args, **kwargs):
    pass


warnings.warn = warn


def main(data_path: str) -> None:
    documents = load_pdfs(data_path)
    nodes = chunk_documents(documents)
    print(f"{len(nodes)} chunks sémantiques générés")

    vectors = embed_texts([n.get_content(metadata_mode=MetadataMode.NONE) for n in nodes])
    n = store_nodes(nodes, vectors)
    print(f"Terminé : {n} chunks stockés dans Qdrant")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "...../data/documents")
