"""The Chroma vector store.

In phase 1 this is the only database. There is no documents table, so a chunk's
metadata is the sole record that a document exists — which is why `chunker.py`
is careful about what goes into it.

Opening a store is cheap (~8 ms) and safe to do per request: chromadb keeps one
shared System per persist path, so many `Chroma` objects over the same directory
resolve to a single underlying client rather than competing for it.
"""

from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings

from app.config import settings
from app.embeddings import build_embeddings

# Named rather than left as Chroma's "langchain" default, so the collection is
# identifiable on disk and a future second collection is unambiguous.
COLLECTION_NAME = "documents"


def build_vector_store(embeddings: Embeddings | None = None) -> Chroma:
    """Open the on-disk collection. Pass `embeddings` to stub the model in tests."""
    settings.chroma_dir.mkdir(parents=True, exist_ok=True)
    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings or build_embeddings(),
        persist_directory=str(settings.chroma_dir),
    )
