"""Reading the document catalogue back out of Chroma.

Phase 1 has no documents table, so the catalogue is *derived*: every chunk
carries `document_id` and `filename`, and a document exists exactly when at
least one of its chunks does. That is why `chunker.py` is strict about
metadata — these functions have nothing else to read.

Phase 2 replaces both functions with a query against `documents`, at which
point the endpoints keep their shape and only this file changes.
"""

from dataclasses import dataclass

from langchain_core.vectorstores import VectorStore

from app.vectorstore import build_vector_store


@dataclass(frozen=True)
class DocumentSummary:
    document_id: str
    filename: str
    page_count: int
    chunk_count: int


def list_documents(*, vector_store: VectorStore | None = None) -> list[DocumentSummary]:
    """Every stored document, one entry each, ordered by filename.

    Ordered by filename because chunk metadata holds no upload timestamp;
    without one, Chroma's own ordering is an implementation detail and the
    picker would reshuffle between requests.
    """
    store = vector_store or build_vector_store()

    # Metadata only. The chunk text and its 3072-float vector are both
    # irrelevant here, and asking for either would move megabytes across the
    # process to build a dropdown.
    metadatas = store.get(include=["metadatas"])["metadatas"] or []

    grouped: dict[str, dict] = {}
    for meta in metadatas:
        document_id = meta.get("document_id")
        if not document_id:
            continue
        entry = grouped.setdefault(
            str(document_id),
            {"filename": str(meta.get("filename") or "document.pdf"), "pages": 0, "chunks": 0},
        )
        entry["chunks"] += 1
        # The highest page number that produced a chunk. This is the page count
        # of the *text* we hold, which undercounts a document whose last pages
        # are images: those pages yield no chunk, so nothing here records them.
        entry["pages"] = max(entry["pages"], int(meta.get("page") or 0))

    return sorted(
        (
            DocumentSummary(
                document_id=document_id,
                filename=entry["filename"],
                page_count=entry["pages"],
                chunk_count=entry["chunks"],
            )
            for document_id, entry in grouped.items()
        ),
        key=lambda summary: (summary.filename.lower(), summary.document_id),
    )


def document_exists(document_id: str, *, vector_store: VectorStore | None = None) -> bool:
    """Whether any chunk carries this `document_id`.

    `include=[]` returns ids and nothing else, and `limit=1` stops at the first
    hit — so this stays a constant-cost check no matter how large the document.
    """
    if not document_id:
        return False

    store = vector_store or build_vector_store()
    found = store.get(where={"document_id": document_id}, limit=1, include=[])
    return bool(found["ids"])
