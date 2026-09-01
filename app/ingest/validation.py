"""Upload validation — runs before anything touches disk."""

from app.errors import FileTooLargeError, NotAPdfError

# Every PDF begins with this, per the spec's header rule. We check the bytes
# rather than the extension because the extension is attacker-chosen and tells
# us nothing about the content.
PDF_MAGIC = b"%PDF-"


def validate_pdf(data: bytes, *, max_bytes: int) -> None:
    """Raise if `data` is not a plausibly-valid PDF within the size limit."""
    if not data:
        raise NotAPdfError("The uploaded file is empty.")

    if len(data) > max_bytes:
        limit_mb = max_bytes / (1024 * 1024)
        actual_mb = len(data) / (1024 * 1024)
        raise FileTooLargeError(
            f"File is {actual_mb:.1f} MB; the limit is {limit_mb:.1f} MB."
        )

    if not data.startswith(PDF_MAGIC):
        raise NotAPdfError("File is not a PDF (missing the %PDF- header).")
