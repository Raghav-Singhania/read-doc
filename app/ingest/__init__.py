"""PDF ingest: bytes in, stored vectors out."""

from app.ingest.pipeline import IngestResult, ingest_pdf

__all__ = ["IngestResult", "ingest_pdf"]
