"""`/api/chat` — ask a question about one document."""

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from app.api.schemas import ChatRequest, ChatResponse, ErrorResponse
from app.documents import document_exists
from app.errors import DocumentNotFoundError
from app.rag import Turn, answer_question

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post(
    "",
    response_model=ChatResponse,
    summary="Ask a question about a document",
    responses={
        404: {"model": ErrorResponse, "description": "DOCUMENT_NOT_FOUND"},
        422: {"model": ErrorResponse, "description": "INVALID_REQUEST"},
        502: {"model": ErrorResponse, "description": "EMBEDDING_FAILED"},
        503: {"model": ErrorResponse, "description": "ANSWER_UNAVAILABLE"},
    },
)
async def chat(request: ChatRequest) -> ChatResponse:
    """Answer `question` about `document_id`, given the client's `history`.

    This handler stays deliberately thin — check the document exists, map the
    wire types to domain types, delegate. All the retrieval and prompting lives
    in `app.rag.answer_question`, which section 4 implements; until it does,
    this returns 503 `ANSWER_UNAVAILABLE`.
    """
    # Checked here rather than inside the chain: a missing document is an
    # addressing mistake by the caller (404), which is a different thing from a
    # document that exists but holds no answer (a 200 with a refusal). Without
    # this, an unknown id would retrieve zero chunks and get a plain "I don't
    # know" — indistinguishable from a real document that says nothing useful.
    if not await run_in_threadpool(document_exists, request.document_id):
        raise DocumentNotFoundError(
            f"No document with id {request.document_id!r}. It may never have "
            "been uploaded, or its upload may have failed."
        )

    history = [
        Turn(role=message.role, content=message.content)
        for message in request.history
    ]

    # Blocking: retrieval reads Chroma from disk and generation waits on
    # Gemini. Off the event loop, as with the ingest path.
    answer = await run_in_threadpool(
        answer_question, request.document_id, request.question, history
    )

    return ChatResponse(answer=answer)
