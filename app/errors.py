"""Failures the ingest and chat paths can raise.

Each carries the machine-readable `code` the API returns and the HTTP status it
maps to, so section 3's endpoints can turn any of these into a response without
a chain of isinstance checks.
"""


class AppError(Exception):
    """Base for every failure we deliberately surface to the client."""

    code = "INTERNAL_ERROR"
    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class FileTooLargeError(AppError):
    code = "FILE_TOO_LARGE"
    status_code = 413


class NotAPdfError(AppError):
    """Raised when the bytes are empty or don't start with the PDF header."""

    code = "NOT_A_PDF"
    status_code = 415


class PdfUnreadableError(AppError):
    """Header says PDF, but the parser could not open it."""

    code = "PDF_UNREADABLE"
    status_code = 422


class NoTextExtractedError(AppError):
    """A readable PDF that yielded almost no text — typically a page of scans.

    OCR is out of scope, so this is a dead end rather than something to retry.
    """

    code = "NO_TEXT_EXTRACTED"
    status_code = 422


class DocumentNotFoundError(AppError):
    code = "DOCUMENT_NOT_FOUND"
    status_code = 404


class EmbeddingError(AppError):
    """The embedding or vector-store call failed — an upstream fault, not the
    user's, hence 502."""

    code = "EMBEDDING_FAILED"
    status_code = 502


class AnswerUnavailableError(AppError):
    """Answer generation is not wired up yet — section 4 builds the chain.

    TEMPORARY. `app/rag/chain.py` raises this so `POST /api/chat` has an
    honest reply (503, "not ready") instead of a 500 traceback while the
    endpoint and the chain land in different sections. Delete both this class
    and the raise once `answer_question` returns a real answer.
    """

    code = "ANSWER_UNAVAILABLE"
    status_code = 503
