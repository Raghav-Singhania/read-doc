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
