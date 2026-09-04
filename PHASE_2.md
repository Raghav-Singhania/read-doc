# Phase 2 — Build List

Everything phase 1 deferred: question condensing, citations, multi-document
chat, AI insights, a relational DB behind the vectors, async ingest with status
polling, server-side conversations, metrics.

**Scope:** SQLite via SQLAlchemy async (no Postgres) · ingest moved off the
request onto FastAPI `BackgroundTasks`, not a broker — one process, so a
restart mid-ingest must be recoverable · Chroma unchanged, same collection,
same embedding model · still PDF only · still plain HTML + vanilla JS.

**Order — cheapest first.** Sections 1–4 add no dependency and no schema: they
are prompt, chain and frontend work over what phase 1 already stores. Section 5
is the dividing line — everything after it needs the DB, a migration tool and a
backfill. Within 1–4 the sort is by moving parts, so condensing (one prompt, one
retriever swap, no wire change) leads and insights (new prompt, structured
output, a cache, an endpoint, a panel) trails. If a visible win matters more
than the cheapest one, start at 2 instead.

Unlike phase 1, each section carries its own frontend work rather than deferring
it to one screen-by-screen section. Every section below is shippable alone.

Phase 1 sections 6–7 (tests, docs) are still open. They are prerequisites, not
phase 2 work.

---

## 1. Question condensing

*No new dependency (`langchain-classic` is installed), no schema change, no UI.*

- [x] Condense prompt in a versioned file (`app/rag/prompts/condense_v1.py`) —
      rewrite a follow-up into a standalone question, change nothing else
- [x] `create_history_aware_retriever` in place of the bare retriever
- [x] Skip condensing when history is empty — saves a model call per first
      question and removes any chance of rewriting a question that needed no
      rewriting
- [x] Log the rewritten question. Phase 1's note stands: a condensing step that
      misfires retrieves the wrong chunks silently, and without the rewrite
      recorded there is nothing to look at afterwards

## 2. Citations

*Chain and wire schema only. Phase 1 already stores every field this needs.*

- [ ] Return `context` from the chain instead of discarding it — phase 1
      already carries `page`, `ordinal` and `filename` on every chunk for this
- [ ] `Citation` schema — `document_id`, `filename`, `page`, `ordinal`,
      `snippet`. Snippet trimmed server-side; the full chunk is not the wire
      format
- [ ] Dedupe by page before returning. Two chunks from one page are one
      citation to a reader
- [ ] Answer prompt v2 — a **new versioned file**, `answer_v2.py`, not an edit
      to v1. Asking the model to mark which context it used changes answer
      wording, so the old prompt has to stay readable next to the new one
- [ ] Cited but unused — do not claim a chunk was used because it was
      retrieved. Either the model marks its sources or the response says
      "retrieved", not "cited"
- [ ] Frontend — citations under each answer, page-numbered

## 3. Multi-document chat

*Request schema, Chroma filter, prompt. No storage change.*

- [ ] `document_ids: list[str]` on the chat request, capped. Single-document
      stays the common path, not a special case
- [ ] Chroma filter `{"document_id": {"$in": [...]}}` in place of equality
- [ ] Retrieve per document and merge, rather than raising global `k`. One long
      document otherwise wins every slot and the others contribute nothing to
      an answer that claims to span them
- [ ] Answer prompt must attribute by filename — an answer blending two PDFs
      without saying which said what is the failure phase 1's per-document
      filter existed to prevent
- [ ] Validate every id up front, one `404` naming the missing one
- [ ] Frontend — multi-select in the document picker

## 4. AI insights

*New prompt and endpoint. Cached on disk for now; §5 moves the cache into the
DB and §6 moves generation into the pipeline.*

- [ ] Insight prompt in a versioned file (`insights_v1.py`) — summary, key
      points, topics
- [ ] Structured output (`with_structured_output`) against a pydantic model, so
      a malformed generation fails validation instead of reaching the UI as
      prose in a summary field
- [ ] Generate on first request, then cache as JSON beside the PDF in
      `storage_dir`, keyed by document id and prompt version. Generating per
      request would re-pay for a document that never changes; generating during
      ingest is not available yet, because ingest is still synchronous and the
      user would wait out both calls
- [ ] `GET /api/documents/{id}/insights` — the cached file, or generate, cache
      and return
- [ ] Insight failure caches nothing and returns `502`. It must never touch the
      answer path: a document with no insights is still fully answerable, and
      the reverse is not true
- [ ] Frontend — insights panel on the chat screen

---

**Everything below needs the DB.**

---

## 5. Relational DB

*The expensive one: a new dependency, a migration tool, and a backfill of
existing data.*

- [ ] SQLAlchemy 2.0 async + `aiosqlite`, one session-per-request dependency.
      The handlers are already `async def` (phase 1 section 3), so this needs
      no endpoint rewrites
- [ ] Alembic — migrations from the first table, not retrofitted once there is
      data worth keeping
- [ ] `documents` table — `id` (the UUID phase 1 already generates, reused
      verbatim so no chunk needs re-embedding), `filename`, `byte_size`,
      `page_count`, `chunk_count`, `status`, `error_code`, `created_at`,
      `ingest_ms`, `insights`
- [ ] Backfill — read `list_documents()` once, insert a row per document. This
      is the only chance to do it cheaply: after phase 2 the vectors are no
      longer the source of truth about what exists
- [ ] Rewrite `app/documents.py` against `documents` instead of chunk metadata.
      The endpoints keep their shape — only this file changes
- [ ] Move section 4's insight cache onto the `insights` column and drop the
      JSON files. Same prompt, same shape, one fewer place to look
- [ ] Delete a document for real — row, file, and its chunks by `document_id`
      filter. Phase 1 could not offer this: with no table, a half-deleted
      document was indistinguishable from a failed upload
- [ ] Frontend — delete from the document list

## 6. Async ingest

- [ ] Status state machine — `PENDING → PROCESSING → READY | FAILED`. The row
      is written before any work starts, so a document is listable (and its
      failure explainable) from the first moment it exists
- [ ] `POST /api/documents` returns `202` with `{document_id, status}` after
      validation and save only. Validation stays inline — a non-PDF or an
      oversized file must still fail as a `4xx` on the upload itself, not as a
      job that fails later
- [ ] `GET /api/documents/{id}` — the polling endpoint. Returns status, counts,
      and `error_code` when failed
- [ ] Run `ingest_pdf` from a background task. The function itself does not
      change: phase 1 kept it out of the endpoint for exactly this
- [ ] Generate insights here instead of on first request — the wait is now the
      background task's, not the user's
- [ ] Persist the failure — `FAILED` plus the `AppError` code, so a failed
      ingest is visible in the UI instead of only in the server log
- [ ] Startup recovery — any row left `PROCESSING` is from a process that died.
      Mark it `FAILED` with an `INTERRUPTED` code rather than leaving it polling
      forever. (A broker would requeue instead; that is the upgrade path, and
      this step is the honest cost of not having one)
- [ ] Frontend — poll after the `202`, which turns phase 1's indeterminate
      second progress phase into a real status. Failed uploads listed with their
      reason, retryable

## 7. Server-side chat

- [ ] `conversations` and `messages` tables — role, content, `citations` JSON,
      `created_at`, `latency_ms`
- [ ] `ChatRequest` takes `conversation_id`; the server loads history itself.
      Client-sent `history` is accepted but ignored for one release, so the
      frontend can be updated separately from the API
- [ ] Server-side history window — the same cap phase 1 put on the wire
      (50 messages), now enforced where it cannot be bypassed
- [ ] `POST /api/conversations` and `GET /api/conversations/{id}` — create, and
      read back with messages
- [ ] `GET /api/documents/{id}/conversations` — resume a chat after a reload
- [ ] Frontend — conversation list per document, resumable; stop replaying
      history on every question

## 8. Metrics

*Last because it reads what sections 5–7 write: with no timings recorded there
is nothing to aggregate.*

- [ ] `GET /api/metrics` — documents by status, total chunks, ingest duration
      p50/p95, questions asked, answer latency p50/p95
- [ ] Timings written by the code paths that own them (`documents.ingest_ms`,
      `messages.latency_ms`), not derived from `created_at` gaps
- [ ] Aggregate in SQL, not in Python over every row
- [ ] Decline rate — needs a stored signal, not string-matching the answer for
      "I don't know". See *To decide during build*
- [ ] Frontend — metrics view; unstyled tables are fine

## 9. Tests

Ordered as the sections are, so each can land with its feature.

- [ ] Condensing — a follow-up ("and its author?") retrieves what the
      standalone question would
- [ ] Citations point at pages the answer actually drew on
- [ ] Multi-document — an answer spanning two PDFs attributes both
- [ ] Insights — a malformed generation fails validation and caches nothing;
      answering still works with no insights
- [ ] Migrations run clean on an empty DB and over a phase 1 backfill
- [ ] Status transitions — including the startup recovery of a stranded
      `PROCESSING` row
- [ ] Ingest failure leaves `FAILED` with a code, no file, no vectors
- [ ] **Still declines on irrelevant context** — phase 1's guard test, re-run
      against prompt v2. Citations make a fabricated answer look better
      sourced, not less fabricated

## 10. Docs

- [ ] `README.md` — DB setup, migration command, the `BackgroundTasks` trade
      and what it costs
- [ ] `AI_USAGE.md` entry per section, written as each lands

---

## Not in phase 2

Postgres · a real broker (Celery/RQ) and a separate worker process · streaming
responses · reranking · hybrid search · document versioning.

Out of scope entirely, as in phase 1: auth, OCR, non-PDF formats.

## To decide during build

- Decline signal for the metrics decline rate — a structured `answered: bool`
  from the answer chain, or `retrieved_chunks == 0`. The first costs a schema
  change to the prompt output; the second misses the case where chunks came back
  but held no answer
- Per-document `k` for multi-document retrieval
- Whether insights regenerate on prompt version bump, or only for new uploads
