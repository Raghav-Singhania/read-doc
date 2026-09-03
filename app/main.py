"""FastAPI application entry point.

Run locally with:
    uvicorn app.main:app --reload
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import chat_router, documents_router
from app.api.errors import register_error_handlers
from app.config import settings

app = FastAPI(title="read-doc")

STATIC_DIR = Path(__file__).parent / "static"

register_error_handlers(app)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness check. Also confirms config loaded, since importing this module
    imports `settings`, which validates on import."""
    return {"status": "ok"}


app.include_router(documents_router)
app.include_router(chat_router)


# Mounted LAST and at "/" on purpose. Starlette matches routes in the order they
# were added, so a mount at "/" declared before the API routes would swallow
# every one of them. html=True serves index.html for "/".
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
