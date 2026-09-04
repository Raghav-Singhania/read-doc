"""The embedding model, behind one factory.

Everything that embeds text — ingest today, retrieval in section 4 — goes
through `build_embeddings()`, so there is exactly one place where the model is
named and exactly one seam for tests to stub.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

from langchain_core.embeddings import Embeddings

from app.config import settings

logger = logging.getLogger(__name__)

# DO NOT CHANGE THIS IN A LATER PHASE.
#
# Similarity is only meaningful between vectors from the same model. Switching
# models leaves the stored vectors in the old space and the query in the new
# one; Chroma will still return its k nearest neighbours, so retrieval does not
# error — it just returns near-random chunks, and the only symptom is answers
# quietly getting worse. Changing this means re-embedding every stored chunk.
EMBEDDING_MODEL = "models/gemini-embedding-001"


# Statuses worth a second attempt: the request was fine and the far end was
# not. 500 is on this list because Gemini returns a bare `500 INTERNAL` for
# embedding calls that succeed on retry seconds later — observed while a second
# chat question failed reproducibly on the query embed.
#
# Deliberately absent: 400 (malformed request), 403 (rejected key), 404. Those
# fail identically however many times they are sent, and retrying them turns a
# clear error into a slow one.
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})

# 3 attempts, matching `llm.py`. The waits are short because this runs inside a
# request on a threadpool worker: a user is watching, and the worker is held
# for the duration.
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (0.5, 1.5)


class _RetryingEmbeddings(Embeddings):
    """Retries transient upstream failures around another `Embeddings`.

    This wrapper exists because the retry cannot be configured on the client
    itself. `ChatGoogleGenerativeAI` takes `max_retries`;
    `GoogleGenerativeAIEmbeddings` has no such field, and its `HttpOptions` are
    built from only `base_url`, `api_version`, `headers` and `client_args` — so
    the SDK's own `retry_options` are unreachable through the wrapper. Retrying
    here is the remaining seam.
    """

    def __init__(self, inner: Embeddings) -> None:
        self._inner = inner

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._attempt(self._inner.embed_documents, texts, len(texts))

    def embed_query(self, text: str) -> list[float]:
        # `str(text)` guards the same trap `_query_for_retrieval` fixes at
        # source: a `str` subclass (langchain's `TextAccessor`) fails to match
        # the `str` member of the SDK's pydantic union, and the text is dropped
        # into an empty `Content()` with no error. Normalising at the boundary
        # too, because the symptom — an opaque 500 on a request that looks
        # correct in every log — costs hours to trace back here.
        return self._attempt(self._inner.embed_query, str(text), 1)

    def _attempt(self, call: Callable[[Any], Any], payload: Any, count: int) -> Any:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return call(payload)
            except Exception as exc:
                status = _transient_status(exc)
                if status is None or attempt == MAX_ATTEMPTS:
                    raise
                delay = BACKOFF_SECONDS[attempt - 1]
                logger.warning(
                    "Embedding %d text(s) failed with %d, retrying in %.1fs "
                    "(attempt %d of %d)",
                    count, status, delay, attempt, MAX_ATTEMPTS,
                )
                time.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover


def _transient_status(exc: BaseException) -> int | None:
    """The HTTP status behind `exc` if it is worth retrying, else None.

    The chain is walked rather than the exception typed, because langchain
    re-wraps the SDK's error into a `GoogleGenerativeAIError` whose message
    carries the status as text and whose `__cause__` carries it as a number.
    Reading the number avoids matching on message wording that is not ours.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        code = getattr(current, "code", None)
        if isinstance(code, int) and code in TRANSIENT_STATUSES:
            return code
        current = current.__cause__ or current.__context__
    return None


def build_embeddings() -> Embeddings:
    """A client for the embedding model, wrapped in transient-failure retries.

    Each call builds a fresh one, at roughly 25 ms — the cost of setting up its
    HTTP client. That is deliberate: it sits alongside a Gemini call measured in
    hundreds of milliseconds, so caching it into a process-wide singleton would
    buy a rounding error in exchange for state that outlives any one request.
    """
    # Imported lazily so that unit tests which stub embeddings never pay for
    # the google client import.
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    client = GoogleGenerativeAIEmbeddings(
        model=EMBEDDING_MODEL,
        google_api_key=settings.gemini_api_key,
        # Reaches httpx as its timeout. Same reasoning as `llm.py`: without one,
        # a hung call holds a threadpool worker until the client gives up, and a
        # handful of those starve the pool while the app still looks healthy.
        client_args={"timeout": 30.0},
    )
    return _RetryingEmbeddings(client)
