"""The file store: uploaded PDFs on local disk, addressed by document id.

Phase 1 has no documents table, so this is deliberately dumb — an id in, a path
out. Swapping local disk for object storage later means changing only this file.
"""

import uuid
from pathlib import Path

from app.config import settings


def _validated_id(document_id: str) -> str:
    """Reject anything that is not one of our generated UUIDs.

    Document ids reach `delete` and `path_for` from request bodies, so without
    this a value like `../../.env` would resolve outside the storage directory.
    """
    try:
        return str(uuid.UUID(document_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"Not a valid document id: {document_id!r}") from exc


def path_for(document_id: str) -> Path:
    return settings.storage_dir / f"{_validated_id(document_id)}.pdf"


def save(document_id: str, data: bytes) -> Path:
    path = path_for(document_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def delete(document_id: str) -> None:
    """Remove the stored file. Silent if it was never written."""
    path_for(document_id).unlink(missing_ok=True)
