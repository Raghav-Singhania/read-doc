# read-doc

A RAG app over uploaded PDFs: FastAPI + Chroma + Gemini, with two plain HTML
screens. `README.md` has the architecture and the request flows — read it
before changing anything structural.

## Commands

The virtualenv is `env/`, not `.venv`. Never use a bare `python` or `pip`.

```bash
env/bin/uvicorn app.main:app --reload --port 8000   # run (logs to stderr)
env/bin/pip install -r requirements.txt             # install
PYTHONPATH=. env/bin/python script.py               # scratch scripts need this
```

`--reload` watches `*.py` only. A `.env` change needs a manual restart.

## Ask before calling the Gemini API

Every embedding and generation call spends a shared free-tier budget:
**20 generations per day** for `gemini-3.5-flash`, and **100 embedded texts per
minute** — counted per text in a batch, not per request. A single test run can
exhaust the day.

So: verify with stubs, not live calls. Inject `llm=` and `vector_store=` into
`answer_question`, or `embeddings=`/`vector_store=` into `ingest_pdf`; both
exist as seams for exactly this. Ask first when only a real call will do, and
say how many it will take.

## Conventions

- **Prompts are versioned files** in `app/rag/prompts/`, with the argument for
  each rule in the module docstring. Never reword a shipped prompt in place:
  add `*_v2.py` beside it and promote it in `prompts/__init__.py`. A reworded
  prompt is a behaviour change and has to be reviewable and revertable alone.
- **`EMBEDDING_MODEL` is a one-way door.** Changing it leaves every stored
  vector in the old space and breaks similarity silently — no error, just worse
  answers. `CHAT_MODEL` is safe to change; it re-reads the same vectors.
- **The service layer must not import the web layer.** `app/rag/` and
  `app/ingest/` never import from `app/api/`. That direction is what keeps them
  callable from a worker, a test or a CLI.
- **Blocking work goes on the threadpool.** Handlers are `async def`; Chroma
  reads and Gemini calls run under `run_in_threadpool`, or they stall the event
  loop for every other request.
- **Errors:** raise an `AppError` subclass carrying its own `code` and
  `status_code`; the single handler in `app/api/errors.py` turns any of them
  into the one response envelope. When wrapping an upstream failure, log the
  cause with `logger.exception` — a shared code is right for the client and
  useless for debugging.
- **Config validates at import** (`settings = Settings()` at module level), so
  bad config fails at startup rather than on a user's first upload. Keep it
  that way; no lazy `get_settings()`.
- **Comments carry the reasoning, not the mechanics.** This codebase explains
  why a choice was made and what breaks otherwise. Match that density; do not
  narrate what the line already says.

## Traps already hit here

- **`str()` anything from `StrOutputParser` before it reaches the Gemini SDK.**
  It returns a `str` subclass, which the SDK's pydantic union converts to an
  empty `Content()` — dropping the text with no error and answering 500. It
  cost a day. `app/rag/chain.py` and `app/embeddings.py` both guard it.
- **Stop the server before deleting `storage/`.** The document catalogue is
  derived from Chroma, whose client caches the collection in-process, so the
  picker keeps showing documents that no longer exist.
- **Retries do not fix a quota.** A 429 with a `retryDelay` of 47s will not
  clear inside a request. Read the error body before adding backoff.

## `AI_USAGE.md` is part of "done"

`AI_USAGE.md` is a graded deliverable recording **how AI was used to build each
feature**. It is not a prompt archive — never paste prompt text into it.

**Trigger:** when you tick a checkbox in the current phase file, add or extend
the `AI_USAGE.md` entry in the same edit. A ticked box with no matching entry
means the entry was skipped — write it while the work is fresh, since the
corrections are the first thing to blur.

Each entry covers:

- What was built, and the files touched
- How Claude was used on it — what it generated, designed, or reviewed, and how
  much was accepted as-is
- Where AI removed manual work
- Corrections and known gaps, stated plainly. A wrong diagnosis that cost time
  belongs here as much as a success.

Keep entries terse: bullets, not essays. The reasoning behind a decision lives
next to the code, not in this file.

## Docs

- `README.md` — setup, architecture, API, assumptions, known limitations
- `PHASE_1.md` / `PHASE_2.md` — what each phase shipped and what it cut. The
  current phase file is the build list; tick items there as they land
- `AI_USAGE.md` — see above

## Not built yet

There are **no automated tests**. Both phase 2 features were verified with
throwaway stub harnesses. If you add tests, they go in `tests/` with stubbed
models — never against the live API.
