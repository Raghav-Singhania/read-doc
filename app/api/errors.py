"""Turning exceptions into the one error shape the API returns.

Every failure response body is `{"error": {"code", "message"}}` — including
FastAPI's own validation failures, which by default use a different shape
(`{"detail": [...]}`). Normalising them here means the frontend has exactly one
error format to parse, not two.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.errors import AppError

logger = logging.getLogger(__name__)


def _envelope(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


def register_error_handlers(app: FastAPI) -> None:
    """Wire the handlers onto `app`."""

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        """Every deliberate failure — 413, 415, 422, 404, 502, 503.

        One handler covers all of them because each `AppError` subclass carries
        its own `code` and `status_code`, so there is no isinstance ladder here
        and adding an error type needs no change to this file.
        """
        # info, not error: these are expected outcomes (a client sent a JPEG,
        # a PDF was a scan). Logging them at error level would bury the 500s
        # that actually need attention.
        logger.info("%s: %s", exc.code, exc.message)
        return _envelope(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """A malformed request body — the wrong type, a missing field, an empty
        question. Reported as 422 `INVALID_REQUEST` in the same envelope."""
        # Pydantic's per-field errors are the useful part; flattened to
        # "field: reason" so a human reads one line rather than nested JSON.
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'][1:]) or 'body'}: {error['msg']}"
            for error in exc.errors()
        )
        return _envelope(
            422, "INVALID_REQUEST", details or "The request body is not valid."
        )
