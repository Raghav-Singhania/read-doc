# AI Usage

How Claude was used to build this project, entry by entry.

## Phase 1 · Section 1 — Setup

**Built:** Python 3.14.5 venv, `requirements.txt`, config module, and a FastAPI
skeleton serving `/health` and static files.

**Files:** `.python-version`, `requirements.txt`, `.env.example`,
`app/config.py`, `app/main.py`, `app/static/index.html`.

### What Claude did

- Selected Python 3.14 by resolving the dependency tree against 3.12, 3.13 and
  3.14 and confirming a cp314 wheel existed for every compiled package.
- Built the environment: `pyenv install 3.14.5`, `python -m venv env`, then
  installed dependencies via `env/bin/pip` so nothing global was touched.
- Wrote `requirements.txt` from `PHASE_1.md`, mapping each named API to its
  package, and added two the plan omits: `python-multipart` (FastAPI needs it for
  `UploadFile`) and `httpx` (Starlette's `TestClient`). Set the env line to
  `python-dotenv`, the installable name behind the `dotenv` import.
- Wrote `.env.example` documenting `GEMINI_API_KEY` and the optional overrides.
- Wrote `app/config.py` — pydantic-settings with module-level
  `settings = Settings()`, so validation runs at startup rather than on first
  use, plus a validator requiring `chunk_overlap < chunk_size`. Accepted as-is.
- Wrote `app/main.py` — `GET /health` plus a `StaticFiles` mount registered
  after the routes, since Starlette matches in registration order and an earlier
  mount would swallow them. Accepted as-is.
- Verified the result: missing key fails at import, `chunk_overlap >=
  chunk_size` is rejected, `/health` returns 200, `/` serves the page, and a
  real `uvicorn` boot answers both.

### Where AI removed manual work

Cross-version wheel checks, mapping each plan item to its providing package, and
the config and endpoint verification.

## Phase 1 · Section 2 — Ingest

**Built:** the ingest pipeline — validate a PDF, save it, extract text per page,
chunk it with metadata, embed with Gemini, write to Chroma, and clean up after
itself when a step fails.

**Files:** `app/errors.py`, `app/embeddings.py`, `app/vectorstore.py`,
`app/ingest/{__init__,validation,files,loader,chunker,pipeline}.py`, `.gitignore`.

### What Claude did

- Checked each library API against the installed version before using it, since
  langchain 1.x moved imports.
- Designed the module split: `embeddings.py` and `vectorstore.py` sit outside
  `ingest/` because section 4's retriever needs them too, and the pipeline sits
  outside the endpoint so moving it onto a queue later is a caller change.
- Wrote `errors.py` with `code` and `status_code` per exception, so section 3
  needs no isinstance ladder. Accepted as-is.
- Ran `PDFPlumberLoader` against a generated PDF instead of trusting its docs.
  Two findings shaped `chunker.py`: `page` is 0-indexed (stored 1-based), and
  the loader attaches `file_path`, an absolute server path that `GET
  /api/documents` would have served to clients. Metadata is now built from four
  explicit keys, not inherited.
- Added a UUID check in `files.py` before any path is built — document ids
  arrive from request bodies, so `../../.env` would otherwise resolve outside
  the storage directory.
- Wrote a dependency-free PDF generator, so tests use real multi-page files with
  known text and no committed binaries.
- Verified end to end with a stubbed embedder against real Chroma: metadata
  shape, ordinals, upsert on re-run, five rejection paths, cleanup after
  failure, traversal guard. Confirmed the live model separately (3072 dims).

### Corrections

- A dead `... if False else None` line survived a first draft of `pipeline.py`.
  Caught on read-back, removed before the file ran.
- The plan lists four error codes; a valid header the parser still can't open is
  neither of the two 4xx cases, so `PDF_UNREADABLE` (422) was added rather than
  overloading either.
- `.gitignore` had no `storage/` entry, which would have committed uploaded PDFs
  and the Chroma database.
- The embedder and store were first `@lru_cache` singletons, justified in
  comments by Chroma file-lock contention. Asked to defend it, Claude measured:
  chromadb shares one System per persist path, so 50 stores resolve to one
  client and 12 concurrent threads hit no errors. The claim was false — the
  caches saved ~34 ms against a Gemini call measured in hundreds, while freezing
  whatever settings were live at first call. Both removed, `get_embeddings`
  renamed `build_embeddings`, and the wrong comment deleted rather than reworded.

### Where AI removed manual work

Version-checking each API, finding the loader's path leak instead of trusting
its docs, building PDF fixtures from scratch, and the full verification pass.
## Phase 1 · Section 3 — API

**Built:** the HTTP layer — upload and ingest a PDF, list what is stored, and
the chat endpoint's request/response contract, all behind one error envelope.

**Files:** `app/api/{__init__,schemas,documents,chat,errors}.py`,
`app/documents.py`, `app/rag/{__init__,chain}.py`, `app/main.py`, `app/errors.py`.

### What Claude did

- Probed the installed libraries before writing against them, rather than
  assuming: `Chroma.get`'s signature, and that `include=[]` returns ids only —
  which is what makes the chat endpoint's existence check constant-cost instead
  of pulling a chunk's 3072-float vector to answer a yes/no question.
- Wrote `app/documents.py`, deriving the catalogue from chunk metadata since
  there is no documents table, grouping by `document_id` for filename, chunk
  count and page count. Ordered by filename: metadata carries no upload
  timestamp, so Chroma's own ordering is an implementation detail that would
  reshuffle the picker between requests.
- Put the blocking work on the threadpool. `ingest_pdf` parses on CPU and waits
  on Gemini for seconds; called directly from an `async def` handler it would
  block the event loop and stall every concurrent request, so the `async`
  requirement in the plan is satisfied with `run_in_threadpool` rather than by
  making the handlers async in name only.
- Capped the upload while reading it, in 64 KiB slices, instead of one
  `await file.read()` — which would have allocated the whole body before the
  size check ever ran. Reading one slice *past* the ceiling is what lets
  `validate_pdf` distinguish "over the limit" from "just fits". Measured: a
  100x-oversized upload pulls 1.05 MB against a 1 MB limit.
- Normalised FastAPI's own validation failures into the same
  `{"error": {code, message}}` envelope as `AppError`. FastAPI defaults to
  `{"detail": [...]}`, which would have left the frontend parsing two error
  shapes. `AppError` needed no isinstance ladder — section 2's `code` and
  `status_code` attributes carry it.
- Built section 4's seam as a real module, `app/rag/chain.py`, with `Turn`
  defined there rather than imported from `app.api.schemas`, so the service
  does not depend on the web layer. Section 4 is now an edit to that one file.
- Verified with 40 assertions against real Chroma and a stubbed embedder: the
  five rejection paths, the error envelope's shape on each, cleanup leaving no
  orphan file, listing round-trip, chat's 404 and its six validation failures,
  and 502 on an embedder that raises. Then a real `uvicorn` boot, including one
  live Gemini upload, and removed the storage directory that test created.

### Where AI removed manual work

Checking `Chroma.get`'s behaviour instead of inferring it, catching the
ignored-kwarg trap, and the whole 40-assertion verification pass including the
live boot.

### Known gap

`page_count` means different things on the two endpoints. The upload reports
what the loader saw; the listing reports the highest page that produced a chunk,
so a document whose last pages are images undercounts there. Recording it rather
than hiding it — the fix is a `page_count` in chunk metadata, which would change
section 2's metadata contract.
