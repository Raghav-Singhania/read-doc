"""`/api/documents` — upload a PDF, and list what has been uploaded."""

from fastapi import APIRouter, File, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.api.schemas import DocumentInfo, DocumentList, ErrorResponse
from app.config import settings
from app.documents import list_documents
from app.ingest import ingest_pdf

router = APIRouter(prefix="/api/documents", tags=["documents"])

# Read the upload in 64 KiB slices rather than one `await file.read()`. The
# size limit is enforced *while* reading, so an oversized upload is rejected
# after one slice past the ceiling instead of after the whole thing is in
# memory. (Uvicorn still receives the bytes off the socket; what this avoids is
# buffering them all.)
READ_CHUNK_BYTES = 64 * 1024


async def _read_capped(file: UploadFile, *, limit: int) -> bytes:
    """Read the upload, stopping one slice after it exceeds `limit`.

    Returning *more* than `limit` is the point: it is what lets `validate_pdf`
    tell an oversized upload from one that just fits, and raise 413. Stopping
    at exactly `limit` would make a 20 MiB file and a 20 GiB file both arrive
    as 20 MiB of bytes, indistinguishable.

    A file at or under the limit is read whole and unchanged. One over it costs
    at most `limit` plus one slice of memory, however large the file really is.
    """
    slices: list[bytes] = []
    total = 0
    while chunk := await file.read(READ_CHUNK_BYTES):
        slices.append(chunk)
        total += len(chunk)
        if total > limit:
            break
    return b"".join(slices)


@router.post(
    "",
    response_model=DocumentInfo,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and ingest a PDF",
    responses={
        413: {"model": ErrorResponse, "description": "FILE_TOO_LARGE"},
        415: {"model": ErrorResponse, "description": "NOT_A_PDF"},
        422: {"model": ErrorResponse, "description": "PDF_UNREADABLE or NO_TEXT_EXTRACTED"},
        502: {"model": ErrorResponse, "description": "EMBEDDING_FAILED"},
    },
)
async def create_document(file: UploadFile = File(...)) -> DocumentInfo:
    """Validate, save, load, chunk, embed and store — inline, in this request.

    Phase 1 has no queue, so the client waits out the embedding call. Every
    failure path in `ingest_pdf` cleans up after itself, so a non-2xx here
    leaves nothing on disk and no vectors in Chroma.
    """
    data = await _read_capped(file, limit=settings.max_upload_bytes)

    # `ingest_pdf` is synchronous and blocks for seconds: pdfplumber parses on
    # CPU and the embedding call waits on network. Calling it directly from an
    # async handler would block the event loop and stall every other request
    # for the duration, so it runs on the threadpool instead.
    result = await run_in_threadpool(
        ingest_pdf, data, file.filename or "document.pdf"
    )

    return DocumentInfo(
        document_id=result.document_id,
        filename=result.filename,
        page_count=result.page_count,
        chunk_count=result.chunk_count,
    )


@router.get(
    "",
    response_model=DocumentList,
    summary="List stored documents",
)
async def get_documents() -> DocumentList:
    """Every stored document, derived from chunk metadata.

    There is no documents table in phase 1 — see `app/documents.py`.
    """
    # Chroma reads from disk, which blocks; same reasoning as the upload path.
    summaries = await run_in_threadpool(list_documents)

    return DocumentList(
        documents=[
            DocumentInfo(
                document_id=summary.document_id,
                filename=summary.filename,
                page_count=summary.page_count,
                chunk_count=summary.chunk_count,
            )
            for summary in summaries
        ]
    )
