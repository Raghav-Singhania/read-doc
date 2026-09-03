"""Request and response bodies for the HTTP API.

Kept separate from the dataclasses the services return (`IngestResult`,
`DocumentSummary`, `Turn`): the service layer stays free to carry fields we do
not publish, and the wire format stays free to change without touching the
pipeline. The endpoints do the mapping, in both directions.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

# Constraints go in `Annotated[...]` rather than in `Field(...)`. Pydantic v2
# accepts `strip_whitespace` on `Field` but ignores it (it is a deprecated
# extra kwarg, warning only), which would let a question of pure whitespace
# through `min_length=1` and on to the model as an empty string.
Question = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000)
]
DocumentId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
MessageText = Annotated[str, StringConstraints(min_length=1, max_length=20_000)]


class DocumentInfo(BaseModel):
    """One stored document. Returned both by the upload and by the listing, so
    the frontend can render a freshly uploaded document with the same code that
    renders the picker."""

    document_id: str
    filename: str
    page_count: int
    chunk_count: int


class DocumentList(BaseModel):
    """An object, not a bare array, so pagination or counts can be added later
    without breaking every client that reads the response."""

    documents: list[DocumentInfo]


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: MessageText


class ChatRequest(BaseModel):
    document_id: DocumentId
    question: Question
    # The client holds the conversation and replays it (phase 1 stores no
    # history server-side), which means its length is client-controlled and
    # feeds straight into a prompt. The cap is what keeps one request from
    # driving an unbounded number of tokens.
    history: list[ChatMessage] = Field(default_factory=list, max_length=50)


class ChatResponse(BaseModel):
    answer: str


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    """Every failure the API returns, in one shape.

    `code` is for the frontend to branch on, `message` is for a human to read.
    Declared here so it shows up in the OpenAPI schema rather than only in
    hand-written docs.
    """

    error: ErrorDetail
