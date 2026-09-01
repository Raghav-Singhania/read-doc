"""The embedding model, behind one factory.

Everything that embeds text — ingest today, retrieval in section 4 — goes
through `build_embeddings()`, so there is exactly one place where the model is
named and exactly one seam for tests to stub.
"""

from langchain_core.embeddings import Embeddings

from app.config import settings

# DO NOT CHANGE THIS IN A LATER PHASE.
#
# Similarity is only meaningful between vectors from the same model. Switching
# models leaves the stored vectors in the old space and the query in the new
# one; Chroma will still return its k nearest neighbours, so retrieval does not
# error — it just returns near-random chunks, and the only symptom is answers
# quietly getting worse. Changing this means re-embedding every stored chunk.
EMBEDDING_MODEL = "models/gemini-embedding-001"


def build_embeddings() -> Embeddings:
    """A client for the embedding model.

    Each call builds a fresh one, at roughly 25 ms — the cost of setting up its
    HTTP client. That is deliberate: it sits alongside a Gemini call measured in
    hundreds of milliseconds, so caching it into a process-wide singleton would
    buy a rounding error in exchange for state that outlives any one request.
    """
    # Imported lazily so that unit tests which stub embeddings never pay for
    # the google client import.
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    return GoogleGenerativeAIEmbeddings(
        model=EMBEDDING_MODEL,
        google_api_key=settings.gemini_api_key,
    )
