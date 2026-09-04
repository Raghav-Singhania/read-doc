# Phase 2 — Build List

Two features, both shipped: the question condensing and citations that phase 1
deferred. Everything else phase 1 deferred stays deferred — see *Not in phase
2*.

**Scope:** prompt, chain and frontend work only · no new dependency, no schema,
no relational DB · Chroma unchanged, same collection, same embedding model ·
still PDF only · still plain HTML + vanilla JS.

Phase 1 sections 6–7 (tests, docs) are still open, and phase 2 adds to that
debt rather than clearing it — both sections below were verified with throwaway
stub harnesses, not with tests in the repo.

---

## 1. Question condensing

Landed in `dcc74ad`. A follow-up is rewritten to stand alone before it is
embedded, so "and who wrote it?" no longer retrieves on five words with no
subject in them.

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

Landed in `fbaad6a`, together with the fix for the bug section 1 introduced:
`StrOutputParser` returns a `str` subclass, which the Gemini SDK's pydantic
union silently converted to an empty `Content()`, so every follow-up question
failed with a 502. See `AI_USAGE.md`.

- [x] Return `context` from the chain instead of discarding it — phase 1
      already carries `page`, `ordinal` and `filename` on every chunk for this
- [x] `Citation` schema — `document_id`, `filename`, `page`, `ordinal`,
      `snippet`. Snippet trimmed server-side; the full chunk is not the wire
      format
- [x] Dedupe by page before returning. Two chunks from one page are one
      citation to a reader
- [x] Answer prompt v2 — a **new versioned file**, `answer_v2.py`, not an edit
      to v1. Asking the model to mark which context it used changes answer
      wording, so the old prompt has to stay readable next to the new one
- [x] Cited but unused — do not claim a chunk was used because it was
      retrieved. Either the model marks its sources or the response says
      "retrieved", not "cited"
- [x] Frontend — citations under each answer, page-numbered

---

## Not in phase 2

Each of these was planned for phase 2 and cut from it. Kept here as the
starting point for the next phase rather than re-derived later; the fuller
write-up of each is in `fbaad6a`.

**Multi-document chat** — `document_ids` on the chat request, a `$in` Chroma
filter, per-document retrieval merged rather than a raised global `k` (one long
document otherwise wins every slot), attribution by filename in the answer
prompt, multi-select in the picker.

**AI insights** — a versioned `insights_v1.py`, structured output against a
pydantic model, a cache keyed by document and prompt version, a
`GET /api/documents/{id}/insights` endpoint, and insight failure never touching
the answer path.

**Relational DB** — SQLAlchemy async plus `aiosqlite`, Alembic from the first
table, a `documents` table reusing the UUIDs phase 1 already generates so no
chunk needs re-embedding, a backfill from chunk metadata, and real deletion of
a document's row, file and vectors.

**Async ingest** — a `PENDING → PROCESSING → READY | FAILED` state machine, a
`202` plus a polling endpoint, `ingest_pdf` on a background task, persisted
failure codes, and startup recovery for rows stranded by a dead process.

Also the reason a large upload cannot work today: the free tier allows 100
embedded texts per minute and counts each text in a batch, so a document over
~100 chunks fails however it is retried — a 102-chunk upload did. Pacing the
embedding calls is only tolerable once ingest is off the request.

**Server-side chat** — `conversations` and `messages` tables, a
`conversation_id` on the request with history loaded server-side, endpoints to
create and read back a conversation, and a resumable transcript after reload.

**Metrics** — `GET /api/metrics` over documents by status, chunk totals and
ingest/answer latency percentiles, with timings written by the code paths that
own them and aggregated in SQL.

**Tests** — condensing, citations, and a regression test for the `str`-subclass
bug that made every follow-up fail silently. Plus phase 1's own open list, and
its guard test re-run against prompt v2: citations make a fabricated answer look
better sourced, not less fabricated.

**Docs** — the `README.md` rewrite. `AI_USAGE.md` is current for both sections
above.

Out of scope entirely, as in phase 1: Postgres · a real broker (Celery/RQ) and
a separate worker · streaming responses · reranking · hybrid search · document
versioning · auth · OCR · non-PDF formats.

## Open questions

- `CHUNK_SIZE` against the embedding quota. Larger chunks mean fewer texts per
  document and coarser retrieval; the current 1000 puts any document over
  ~80,000 characters past the free-tier per-minute limit
- Decline rate signal, if metrics are ever built — a structured `answered: bool`
  from the answer chain, or `retrieved_chunks == 0`. The first costs a schema
  change to the prompt output; the second misses the case where chunks came back
  but held no answer
- Per-document `k`, if multi-document retrieval is ever built
