"""PDF text extraction: bytes on disk in, one Document per page out."""

from pathlib import Path

from langchain_core.documents import Document

from app.errors import NoTextExtractedError, PdfUnreadableError

# Below this many characters across the whole document there is nothing worth
# embedding — the file is almost certainly scanned images, and OCR is out of
# scope. Failing here is far better than accepting the upload and producing a
# document that silently answers nothing.
MIN_TEXT_CHARS = 100


def load_pages(path: Path, *, filename: str) -> list[Document]:
    """Extract one `Document` per page.

    Raises `PdfUnreadableError` if the parser cannot open the file, and
    `NoTextExtractedError` if it opens but holds almost no text.
    """
    # Imported here, not at module scope: pdfplumber pulls in pdfminer, which
    # costs noticeable import time, and nothing else in the app needs it.
    from langchain_community.document_loaders import PDFPlumberLoader

    try:
        pages = PDFPlumberLoader(str(path)).load()
    except Exception as exc:
        raise PdfUnreadableError(
            f"Could not read {filename}. The file may be corrupt or encrypted."
        ) from exc

    total_chars = sum(len(page.page_content.strip()) for page in pages)
    if total_chars < MIN_TEXT_CHARS:
        raise NoTextExtractedError(
            f"No readable text found in {filename}. Scanned or image-only PDFs "
            "are not supported."
        )

    return pages
