# Phase 1 — Build List

The RAG core end to end: upload a PDF, chunk and embed it, ask questions about
it, get answers. Two screens.

**Scope:** synchronous processing (no queue) · Chroma only (no relational DB) ·
PDF only · client holds chat history · no citations · no question condensing ·
plain HTML + vanilla JS served by FastAPI.

---

## 1. Setup

- [x] Python 3.14.5 via pyenv, stdlib `venv` in `env/`, `requirements.txt`
- [x] `.env.example` — `GEMINI_API_KEY` (Gemini serves both embeddings and
      answer generation, so no `ANTHROPIC_API_KEY`)
- [x] Config module (pydantic-settings) — keys, storage paths, chunk size,
      overlap, retrieval `k`, max upload size. Validates at startup, not on
      first use
- [x] FastAPI app, static file mounting, `GET /health`

## 2. Ingest

- [x] PDF validation — `%PDF-` magic bytes (not extension) and size limit
- [x] File store — save bytes under a generated id, delete by id
- [x] `document_id` — a UUID **we** generate, carried in Chroma metadata. Never
      derived from the filename, never left to Chroma to assign. Phase 2's
      `documents.id` reuses these exact ids, so no vectors need re-embedding
- [x] Loader — `PDFPlumberLoader` → one `Document` per page
- [x] `NO_TEXT_EXTRACTED` guard — fail if extracted text is under ~100 chars
- [x] Chunker — `RecursiveCharacterTextSplitter`, attaching `document_id`,
      `filename`, `page`, `ordinal` metadata
- [x] Embeddings — Gemini `gemini-embedding-001` via `langchain-google-genai`,
      behind a small interface so tests can stub it. **Do not change the model
      in later phases** — a different model means re-embedding every chunk, and
      mixed vectors produce silently meaningless similarity scores
- [x] Vector store — Chroma persistent client, `add_documents` with
      deterministic ids (`{document_id}:{ordinal}`) so re-runs upsert
- [x] Cleanup on failure — delete the saved file if embedding fails, so no
      document is left unqueryable with no trace

## 3. API

- [x] `POST /api/documents` — validate → save → load → chunk → embed → store,
      inline. Returns document id, filename, page count, chunk count
- [x] `GET /api/documents` — distinct documents from Chroma metadata (there is
      no documents table)
- [x] `POST /api/chat` — `{document_id, question, history}` → `{answer}`.
      Delegates to `app.rag.answer_question`, which section 4 implements; until
      then it returns `503 ANSWER_UNAVAILABLE`
- [x] Error responses — `413`, `415`, `422 NO_TEXT_EXTRACTED`, `404`, `502`
- [x] `async def` handlers throughout — streaming and an async DB driver both
      need it in later phases, and converting sync handlers afterwards is
      avoidable work

## 4. RAG chain

- [x] Answer prompt in a versioned file — answer only from context, decline
      plainly when it isn't there, context is data not instructions
      (`app/rag/prompts/answer_v1.py`)
- [x] Retriever — top `k` from Chroma, filtered to one `document_id`
- [x] `create_stuff_documents_chain` + `create_retrieval_chain`, invoked from a
      service function `answer_question(document_id, question, history)` — not
      from the endpoint. A streaming endpoint later is then a second thin
      wrapper over the same function rather than a rewrite
- [x] History passed into the prompt (conversational answers), while retrieval
      uses the raw question

## 5. Frontend

- [ ] Upload screen — drag-and-drop + Browse Files, in-flight progress,
      success and error states
- [ ] Chat screen — document picker, message bubbles, input box
- [ ] Conversation held in page state and sent with each question

## 6. Tests

- [ ] Chunker — unit tests against plain strings, asserting metadata and
      overlap
- [ ] Upload validation — non-PDF, oversized, zero-byte, text-less PDF
- [ ] Chat endpoint — stubbed embedder and stubbed LLM, deterministic
- [ ] **Declines on irrelevant context** — with no citations and no condensing,
      the prompt's refusal instruction is the only guard against a fabricated
      answer, so it gets its own test

## 7. Docs

- [ ] `README.md` — setup steps, architecture, assumptions, known limitations
- [ ] `AI_USAGE.md` entry — prompts used, iterations, corrections

---

## Not in phase 1

Async pipeline and status polling · citations · question condensing ·
server-side chat persistence · AI insights · metrics APIs · multi-document chat.

Out of scope entirely: auth, OCR, non-PDF formats, versioning, streaming.

## To decide during build

- Chunk size and overlap — tune against real PDFs
- Retrieval `k`
