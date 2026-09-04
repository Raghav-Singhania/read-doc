# read-doc

Upload a PDF, ask questions about it, get answers grounded in that document
with the pages they came from. A retrieval-augmented generation (RAG) app built
on FastAPI, Chroma and Gemini, served as two plain HTML screens.

Answers come only from the uploaded document. When the retrieved excerpts do
not contain an answer, the model says so rather than filling the gap from its
training data.

## How it works

**Ingest** — one request, start to finish:

```
PDF bytes → validate (%PDF- magic, size cap)
          → save under a generated UUID
          → PDFPlumberLoader, one Document per page
          → RecursiveCharacterTextSplitter, tagged with page + ordinal
          → Gemini embeddings
          → Chroma (id = {document_id}:{ordinal})
```

Any failure deletes the saved file, so a document is never left unqueryable
with no trace. Chroma is written only after every chunk embeds, so a partial
failure leaves no vectors behind either.

**Answering** — one request, up to two model calls:

```
question + history → condense to a standalone question   (skipped if no history)
                   → embed, retrieve top k from ONE document
                   → excerpts labelled [page N] into the answer prompt
                   → answer + a trailing SOURCES: line naming the pages used
                   → strip that line, dedupe pages, return answer + citations
```

The retrieval filter is per-document, so a question about one PDF never
retrieves from another.

**Module map**

| Path | Holds |
| --- | --- |
| `app/main.py` | app, `/health`, static mount (registered last, on purpose) |
| `app/config.py` | pydantic-settings; validates at import, so bad config fails at startup |
| `app/api/` | routers, wire schemas, one error envelope |
| `app/ingest/` | validation, file store, loader, chunker, pipeline |
| `app/rag/` | the chain, and versioned prompts that ship with it |
| `app/embeddings.py`, `app/llm.py` | one place naming each model, one seam for stubs |
| `app/vectorstore.py`, `app/documents.py` | Chroma, and the document catalogue read back out of it |
| `app/static/` | the two screens — vanilla JS, no build step |

There is no relational database. A document exists exactly when at least one of
its chunks does, so `GET /api/documents` is derived from chunk metadata.

## Setup

Requires Python 3.14.5 (see `.python-version`) and a Gemini API key from
[aistudio.google.com/apikey](https://aistudio.google.com/apikey).

```bash
pyenv install 3.14.5          # if you don't have it
python -m venv env
env/bin/pip install -r requirements.txt

cp .env.example .env          # then fill in GEMINI_API_KEY
```

Run it:

```bash
env/bin/uvicorn app.main:app --reload --port 8000
```

Then open <http://127.0.0.1:8000>. Uploaded PDFs and the Chroma database live
under `storage/`, which is gitignored — delete it to start clean.

## API

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/documents` | multipart PDF upload; ingests inline, returns id, filename, page and chunk counts |
| `GET` | `/api/documents` | every stored document, ordered by filename |
| `POST` | `/api/chat` | `{document_id, question, history}` → `{answer, citations, citation_basis}` |
| `GET` | `/health` | liveness, and proof config loaded |

Every failure returns the same shape — `{"error": {"code", "message"}}` — with
`code` for the client to branch on: `FILE_TOO_LARGE`, `NOT_A_PDF`,
`PDF_UNREADABLE`, `NO_TEXT_EXTRACTED`, `DOCUMENT_NOT_FOUND`, `INVALID_REQUEST`,
`EMBEDDING_FAILED`, `ANSWER_FAILED`.

`citation_basis` distinguishes `cited` (the model named these pages) from
`retrieved` (it did not, so these are only what the search returned). The
client labels the two differently, because only one is a claim about the answer.

## Configuration

`GEMINI_API_KEY` is required; everything else has a working default.

| Variable | Default | Notes |
| --- | --- | --- |
| `GEMINI_API_KEY` | — | used for both embeddings and generation |
| `STORAGE_DIR` | `storage/documents` | uploaded PDFs |
| `CHROMA_DIR` | `storage/chroma` | vector database |
| `CHUNK_SIZE` | `1000` | characters; must exceed `CHUNK_OVERLAP` |
| `CHUNK_OVERLAP` | `200` | characters |
| `RETRIEVAL_K` | `4` | chunks retrieved per question |
| `MAX_UPLOAD_BYTES` | `20971520` | 20 MiB |

Models are pinned in code, not configured. `gemini-3.5-flash` (generation) is
safe to change — it re-reads the same vectors. `gemini-embedding-001` is not:
switching it silently invalidates every stored vector and needs a full
re-ingest. Both have reasoning recorded beside them.

Prompts ship as versioned files under `app/rag/prompts/`, with the argument for
each rule alongside. `answer_v2` is active; `answer_v1` is kept to diff against.

## Assumptions

- **Single user, no auth.** A local tool; anyone who can reach the port can
  read every uploaded document.
- **Text-based PDFs.** A scanned page yields no text and is rejected with
  `NO_TEXT_EXTRACTED` — there is no OCR.
- **One document per question.** Answers never span two PDFs.
- **The uploaded PDF is untrusted input.** Excerpts reach the model as data,
  never as instructions, because anyone who can hand you a PDF can put
  "ignore your instructions" inside it.
- **Conversation lives in the browser.** The client replays history with each
  question, capped at 50 messages; nothing is stored server-side.

## Known limitations

- **Ingest is synchronous.** The upload request waits out extraction and
  embedding, and reports no progress once the bytes are sent. There is no job
  queue and no status endpoint.
- **Free-tier quotas bite.** Embedding allows 100 texts per minute and counts
  *each text in a batch*, so a document over ~100 chunks cannot ingest in one
  pass however often it is retried — at the default `CHUNK_SIZE` that is around
  80,000 characters. Generation allows 20 requests per day for
  `gemini-3.5-flash`, and a follow-up question costs two (condense + answer).
  Raising `CHUNK_SIZE` trades retrieval precision for headroom.
- **No automated tests.** Behaviour was verified with throwaway stub harnesses,
  which is not the same thing. This is the largest gap.
- **Citations depend on the model complying.** If it omits the `SOURCES:` line,
  the response falls back to `retrieved` and claims nothing. If it names a page
  that was never retrieved, the citation is dropped — but a sentence in the
  answer prose can still mention that page.
- **Reload loses the conversation**, and changing document clears it.
- Multi-document chat, AI insights, server-side history and metrics were planned
  and cut. See `PHASE_2.md`.

## Troubleshooting

- **Errors show as a bare 502.** The real cause is logged with a traceback to
  the server's stderr. Watch the terminal running uvicorn, or `tee` it to a
  file.
- **Deleting `storage/` while the server runs leaves documents in the picker.**
  The catalogue comes from Chroma, whose client caches the collection in the
  process. Stop the server first, or restart after deleting.
- **`.env` edits appear to do nothing.** `--reload` watches `*.py` only, so
  config changes need a manual restart.

## Docs

- `PHASE_1.md` — the phase 1 build list, and what it deliberately left out
- `PHASE_2.md` — what phase 2 shipped, and what was cut from it
- `AI_USAGE.md` — how AI was used to build each feature, including the
  corrections

## License

[MIT](LICENSE)
