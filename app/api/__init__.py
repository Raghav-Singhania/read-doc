"""The HTTP layer: routers, wire schemas, and the error envelope.

Handlers here are thin on purpose — validate, delegate to a service, map the
result. Anything with logic in it belongs in `app/ingest/`, `app/documents.py`
or `app/rag/`, so it stays callable from a worker or a test with no request.
"""

from app.api.chat import router as chat_router
from app.api.documents import router as documents_router

__all__ = ["chat_router", "documents_router"]
