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
