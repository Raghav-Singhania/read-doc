# Design Decisions

What was decided and why, in one line each. Behaviour lives in
[FLOWS.md](FLOWS.md); this file is only the reasoning.

**Status:** requirements and stack agreed 2026-08-31. LLD open.

---

## 1. Scope

### 1.1 No authentication
No users table. Documents, sessions and metrics are global — the brief never
mentions users. No `owner_id`, no ownership filters. Adding auth later is one
request dependency plus a per-query filter.

### 1.2 No OCR
Scanned PDFs unsupported; Tesseract is a system binary, slow, and never asked
for. Not the same as no handling: under ~100 extracted characters →
`failed` / `NO_TEXT_EXTRACTED`, or an empty string reaches the LLM and it
fabricates a summary.

### 1.3 PDF only
All the real extraction difficulty lives in PDF; one format done well beats
several done shallowly. Validated by `%PDF-` magic bytes, not extension. Kept
behind a MIME→extractor map so a format is one module. Chunker and retrieval
are unit-tested against plain strings, since hand-written text fixtures are no
longer valid API input.

### 1.4 Hard delete
Matches what "delete" means to a user; soft delete adds a filter to every query
where one omission resurfaces deleted rows. Requires [2.1](#21-citations-are-self-contained).

Chunks are *not* covered by a DB cascade: they live in LangChain's
`langchain_pg_embedding`, which has no FK to `documents`. Removing them is an
explicit vector-store delete-by-filter that every delete path must call. File
bytes, the document row and insights delete normally.

### 1.5 No streaming yet, but nothing that blocks it
Citations are core, streaming is a bonus — and streaming built first entangles
transport with retrieval. Imposes [2.2](#22-sse-readiness).

### 1.6 Re-upload creates a new document
Versioning restructures the data model: documents become containers, chunks
belong to versions, and sessions must pin a version or answer from content that
differs from what earlier turns cited. High cost, low demo value, bonus-only.
Content hash is still stored, and chunk-level embedding caching makes a repeat
upload near-free.

---

## 2. Constraints these impose

### 2.1 Citations are self-contained
Store snippet text, page and document title at answer time — not a chunk FK.
Hard delete would dangle the FK, and reprocessing with different chunking would
silently repoint it at other text.

### 2.2 SSE readiness
1. RAG logic in a service function (`answer_question`), not the endpoint
2. Streaming comes from LangChain's `.astream()` — no wrapper of our own needed
3. Persistence is a discrete post-generation step, not part of response building
4. Citations parsed from finished answer text, so one code path serves both
5. Service returns a plain result object, not ORM models
6. Async throughout the **API process** (handlers, DB driver, HTTP client).
   Worker tasks may be sync.

### 2.3 Map-reduce summarization
Long PDFs exceed the context window. Summarize per section, then summarize the
summaries. No stage assumes the whole document fits one call.

### 2.4 Document text is untrusted
Extracted text enters prompts, so delimit it and state that context is data, not
instructions. A PDF containing "ignore previous instructions" is realistic input.

### 2.5 Vectors are stamped with the model that produced them
Each chunk records its embedding model and dimension; startup refuses a
mismatch. Vectors from two models are incomparable even at equal dimensions —
scores look valid and mean nothing. Changing model is a re-embed migration, not
a config change.

Stored as a LangChain metadata field, so this is an application-level check with
no DB constraint behind it.

---

## 3. Stack

macOS arm64, Redis running, no Docker, no Postgres at time of choosing.

| Role | Choice |
|---|---|
| Runtime / packaging | Python 3.12, `uv` |
| HTTP layer | FastAPI |
| Validation / schemas | Pydantic v2 |
| Service layer | plain Python modules |
| Data access | SQLAlchemy 2.0 async + asyncpg |
| Relational store | PostgreSQL 16 (local, brew) |
| Vector index | pgvector 0.8.6 via `langchain_postgres.PGVector` (LangChain-managed tables) |
| Schema evolution | Alembic |
| File store | local filesystem behind a storage interface |
| Queue + worker | Celery + Redis |
| Cache | Redis (same instance) |
| PDF extraction | LangChain `PDFPlumberLoader` |
| Chunking | `langchain-text-splitters` |
| LLM | Claude Opus 5 (`claude-opus-5`) via `langchain-anthropic` |
| Embeddings | Gemini `gemini-embedding-001`, 1536d, via `langchain-google-genai` |
| RAG orchestration | LangChain — `create_history_aware_retriever` + `create_retrieval_chain` |
| Config / secrets | pydantic-settings |
| Logging | structlog |
| Tests | pytest, pytest-asyncio, httpx |

Postgres covers the relational store *and* the vector index; Redis covers the
queue *and* the cache. Both deliberate.

**LangChain as the RAG framework** follows the brief, which calls it "highly
recommended". Prompts stay ours (`ChatPromptTemplate`). What it costs, accepted
knowingly: LangChain owns the chunk table, so hard delete loses its DB cascade
([1.4](#14-hard-delete)), the embedding-model stamp loses its DB constraint
([2.5](#25-vectors-are-stamped-with-the-model-that-produced-them)), and
chunk-level metrics query a schema a library version can change. Still ours
either way: the relevance gate (a branch before the chain runs), the
per-document retrieval quota (a custom retriever), citation parsing, and the
five-stage pipeline with its per-stage timings.

---

## 4. Exclusions

Auth · OCR · non-PDF formats · version tracking · soft delete · streaming
(deferred, not designed out).

---

## 5. Open

- **LLD:** schema, endpoint contracts, prompt templates, module layout
- **Bonus features** beyond the core requirements
- **Chunk size and overlap** — tune against real PDFs
- **Relevance-gate threshold** — calibrate on observed scores
