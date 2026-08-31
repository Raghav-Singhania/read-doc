# Flows

Behavioural spec for every request path and background job. Sequence and error
handling only — rationale lives in [DECISIONS.md](DECISIONS.md), and concept
explanations belong in the README's architecture section.

**Keep in sync with the code.** A flow whose steps no longer match the
implementation is worse than no flow doc.

Two processes: the **API** (HTTP, fast, never does slow work) and the **worker**
(no HTTP, consumes jobs). They communicate only via the queue and the database.

---

## 1. Upload a document

**Trigger:** `POST /documents` (multipart file)
**Process:** API only. No AI calls, no full parse. Target < 500ms.

1. Reject if no file, or size over the configured limit → `413`
2. Read magic bytes; reject if not `%PDF-` → `415 UNSUPPORTED_TYPE`
3. Open the PDF header; reject if encrypted → `422 PDF_ENCRYPTED`
4. Probe the first pages for a text layer; if effectively empty →
   `422 NO_TEXT_EXTRACTED` (see [DECISIONS 1.2](DECISIONS.md#12-no-ocr))
5. Compute SHA-256 of the bytes (stored, non-blocking — see
   [DECISIONS 1.6](DECISIONS.md#16-re-upload-creates-a-new-document))
6. Write bytes to the file store under a generated key
7. Insert `documents` row: `status=queued`, filename, size, hash, page count
8. Enqueue a processing job carrying the document id
9. Return `202` with `{document_id, status, estimated_seconds}`

**Failure handling**
- Storage write fails → no row is inserted; `503`
- Row inserted but enqueue fails → row set to `failed` / `ENQUEUE_FAILED`; the
  document is never left silently stuck in `queued`

**Guarantees:** a `202` means the upload is recorded and queued — not processed.

---

## 2. Processing pipeline

**Trigger:** a job on the queue
**Process:** worker only

Each stage writes a `processing_events` row on entry and exit (stage, status,
timestamps, tokens, cost, error). This is the sole data source for the
processing metrics endpoint.

| # | Stage | Does | On permanent failure |
|---|---|---|---|
| 1 | `extracting` | `PDFPlumberLoader` → one `Document` per page; sets page and char counts. Page-level only — the loader gives no char offsets | `CORRUPT_FILE`, `NO_TEXT_EXTRACTED` |
| 2 | `chunking` | `RecursiveCharacterTextSplitter` with overlap. Page propagates from the loader; we attach `document_id`, ordinal and char range as metadata | `CHUNKING_FAILED` |
| 3 | `embedding` | `PGVector.add_documents(...)` — embeds and inserts in one call | `EMBEDDING_FAILED` |
| 4 | `analyzing` | `llm.with_structured_output(...)` (see [4a](#4a-analysis-output)) | `ANALYSIS_FAILED` |
| 5 | `ready` | Set terminal status, record total duration | — |

**Resumability:** stages complete in order and record completion, so a retry
resumes from the last completed stage. Embedding is idempotent **only because we
pass deterministic ids** (`{document_id}:{ordinal}`) to `add_documents`, so a
re-run upserts instead of inserting duplicate chunks. Without explicit ids,
PGVector generates new ones and a retry silently doubles the document's chunks.

**Retry policy**
- *Transient* (429, 5xx, network, timeout) → exponential backoff, capped
  attempts, status stays `processing` with an attempt counter
- *Permanent* (corrupt, no text, unsupported) → terminal `failed` immediately,
  no retries

**Long documents:** the analysis stage summarizes per section, then summarizes
the summaries. No stage may assume the whole document fits one LLM call
([DECISIONS 2.3](DECISIONS.md#23-map-reduce-summarization)).

**Cache:** the embedder is wrapped in `CacheBackedEmbeddings.from_bytes_store`
over a Redis store, keyed by chunk text, so identical content across uploads is
never re-embedded.

### 4a. Analysis output

One structured-output call per section-group, validated against a schema:
short summary, detailed summary, 3–7 key points, category, document type, tags,
named entities (parties, dates, amounts), suggested starter questions.
Validation failure is retried once with the schema error fed back, then
`ANALYSIS_FAILED`.

---

## 3. Read document status / insights

**Trigger:** `GET /documents/{id}`, `GET /documents/{id}/insights`
**Process:** API only. Pure database read — insights are never generated on read.

- `ready` → `200` with the payload
- `queued` / `processing` → `409` with current stage, attempt count, and
  progress
- `failed` → `422` with the error code and human-readable message
- unknown id → `404`

`GET /documents` lists with filter by status, sort, and pagination.

---

## 4. Start a chat session

**Trigger:** `POST /chat/sessions` `{document_ids: [...]}`
**Process:** API only

1. Reject an empty document list → `400`
2. Any id unknown → `404` naming the missing ids
3. Any document not `ready` → `409` naming which, with their statuses
4. Insert `chat_sessions` row plus its document scope
5. Return `201` with session id, document titles, and starter questions drawn
   from the stored insights (no LLM call — they were generated at analysis time)

---

## 5. Ask a question

**Trigger:** `POST /chat/sessions/{id}/messages` `{question}`
**Process:** API only. The whole sequence lives in one service function
(`answer_question`) so a future SSE endpoint reuses it verbatim
([DECISIONS 2.2](DECISIONS.md#22-sse-readiness)).

| # | Step | I/O | Notes |
|---|---|---|---|
| 1 | Load session and last N turns | DB | `404` unknown session |
| 2 | `create_history_aware_retriever` | LLM + Embeddings + DB | One call doing three things: condenses a follow-up into a standalone question (**skipped when history is empty**), embeds it with the same model used at ingest, retrieves. Scoped to the session's documents by metadata filter, with a per-document quota via a custom retriever so one document cannot fill every slot. `score_threshold` drops weakly-matching chunks |
| 3 | Assemble the prompt | pure | `ChatPromptTemplate`, ours. Numbered context blocks labelled with document and page; instructs cite-as-`[n]`, that context is data not instructions, and — see below — that the model must decline when context is empty or unrelated |
| 4 | Generate the answer | LLM | `create_retrieval_chain`. **Always runs**, including when the threshold left no chunks |
| 5 | Parse citations | pure | Map `[n]` back to the numbered chunk. **Discard indexes that were never supplied** — models occasionally invent them |
| 6 | Persist | DB | User message, assistant message, citations, tokens, cost, latency |
| 7 | Return | — | `200` with answer, citations, and usage |

**Citations are self-contained** — each row stores snippet text, page and
document title captured at answer time, not a chunk foreign key
([DECISIONS 2.1](DECISIONS.md#21-citations-are-self-contained)). This matters
more now that chunks live in a table LangChain owns.

**The relevance gate is prompt-enforced, not control flow.** Weak or empty
context still reaches the model, and the prompt is what makes it decline. Two
consequences, both accepted deliberately:

- An unanswerable question costs a full LLM call.
- The prompt must instruct the model to decline when context is **empty or
  unrelated**, not only when an answer is missing from otherwise-relevant
  context. That instruction is the only thing standing between weak retrieval
  and a fabricated answer, so it carries more weight than any other line in the
  prompt and needs its own test.

**Failure handling**
- Generation fails → persist both messages with the assistant marked `failed`,
  or neither. Never leave a user message as an unanswered turn; it corrupts the
  condense step on the next question. Return `503` with a retryable flag
- Embedding provider fails → `503`, nothing persisted
- Condense fails → fall back to the raw question rather than failing the request

---

## 6. Read chat history

**Trigger:** `GET /chat/sessions/{id}/messages`
**Process:** API only. Paginated, oldest first, citations inlined per message.
Messages marked `failed` are returned with their status so a client can render
or hide them.

---

## 7. Delete a document

**Trigger:** `DELETE /documents/{id}`
**Process:** API only

1. Unknown id → `404`
2. Delete the file bytes from the file store
3. **Delete the chunks explicitly** — `PGVector.delete(...)` filtered on
   `document_id` metadata. There is no FK from `langchain_pg_embedding` to
   `documents`, so nothing cascades here
4. Delete the document row; insights cascade
5. Remove the document from every session's scope
6. Any session left with no documents in scope is deleted
7. Chat messages and their citations survive and still render, because citations
   carry their own snippet text
8. Return `204`

**Order matters.** Vectors go first. A failure between steps 3 and 4 leaves a
document with missing chunks, which reprocessing fixes. The reverse order leaves
orphaned vectors with no document — invisible to every endpoint, and they still
surface in retrieval.

See [DECISIONS 1.4](DECISIONS.md#14-hard-delete).

---

## 8. Metrics and health

**Trigger:** `GET /metrics/documents`, `GET /metrics/processing`, `GET /health`
**Process:** API only. Aggregations over `documents` and `processing_events`;
no separate instrumentation path to keep in sync. Chunk-level figures come from
`langchain_pg_embedding` and its `cmetadata` JSONB — a schema LangChain owns and
a version bump can change, so these queries are the most brittle in the system.

- **Documents:** totals by status and type, storage bytes, page and word totals,
  average pages per document, uploads over time
- **Processing:** throughput, p50/p95 per stage and end-to-end, success rate,
  failures grouped by error code, retry counts, queue depth, tokens and cost by
  operation type
- **Health:** database, queue, file store and AI provider reachability, with an
  overall status
