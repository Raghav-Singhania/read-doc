"""The ingest pipeline: validate, save, load, chunk, embed, store.

Phase 1 runs this inline inside the request. That is the deliberate trade in
`PHASE_1.md` — no queue, so the user waits for embedding, but there is also no
worker, no job table and no status polling. The pipeline lives here rather than
in the endpoint so that moving it onto a queue later means calling
`ingest_pdf` from a worker instead of rewriting it.
"""

import uuid
from dataclasses import dataclass
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from app.config import settings
from app.errors import AppError, EmbeddingError, NoTextExtractedError
from app.ingest import files
from app.ingest.chunker import chunk_id, chunk_pages
from app.ingest.loader import load_pages
from app.ingest.validation import validate_pdf


@dataclass(frozen=True)
class IngestResult:
    document_id: str
    filename: str
    page_count: int
    chunk_count: int


def ingest_pdf(
    data: bytes,
    filename: str,
    *,
    embeddings: Embeddings | None = None,
    vector_store: VectorStore | None = None,
) -> IngestResult:
    """Ingest one PDF and return what was stored.

    `embeddings` and `vector_store` are injectable so tests can run the whole
    pipeline without network calls or a database on disk.

    Raises the `AppError` subclasses in `app.errors`; every one of them leaves
    no file and no vectors behind.
    """
    validate_pdf(data, max_bytes=settings.max_upload_bytes)

    # We generate the id, before anything is stored. It is never derived from
    # the filename (two uploads may share one) and never delegated to Chroma
    # (phase 2's documents.id reuses these exact values, so vectors written now
    # stay addressable then without re-embedding).
    document_id = str(uuid.uuid4())

    # Strip any directory component a client may have sent: this string is
    # stored in metadata and rendered back into the UI.
    safe_filename = Path(filename).name or "document.pdf"

    path = files.save(document_id, data)

    try:
        pages = load_pages(path, filename=safe_filename)
        chunks = chunk_pages(
            pages, document_id=document_id, filename=safe_filename
        )
        if not chunks:
            raise NoTextExtractedError(
                f"No readable text found in {safe_filename}."
            )
        _store(chunks, embeddings=embeddings, vector_store=vector_store)
    except Exception:
        # A saved file with no vectors is unreachable: it answers no questions
        # and appears in no listing, because the listing is built from chunk
        # metadata. Deleting it keeps failure from leaving orphans on disk.
        files.delete(document_id)
        raise

    return IngestResult(
        document_id=document_id,
        filename=safe_filename,
        page_count=len(pages),
        chunk_count=len(chunks),
    )


def _store(
    chunks: list[Document],
    *,
    embeddings: Embeddings | None,
    vector_store: VectorStore | None,
) -> None:
    """Embed and write the chunks, mapping upstream faults to `EmbeddingError`.

    Kept separate so the mapping covers only this call: wrapping the whole
    pipeline would report a chunker bug as an upstream embedding failure.
    """
    if vector_store is None:
        from app.vectorstore import build_vector_store

        # Passing `embeddings` through covers both cases: a stub when one was
        # given, and the real model when it was not.
        vector_store = build_vector_store(embeddings)

    try:
        vector_store.add_documents(chunks, ids=[chunk_id(c) for c in chunks])
    except AppError:
        raise
    except Exception as exc:
        raise EmbeddingError(
            "Could not embed the document. The embedding service may be "
            "unavailable or the API key may be rejected."
        ) from exc
