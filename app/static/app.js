/* read-doc — upload and chat, vanilla JS, no build step.
 *
 * Structure: constants, state, helpers, then one section per screen. Every
 * render function reads `state` and rewrites its container, so there is one
 * source of truth and no partial DOM updates to keep in sync.
 */

"use strict";

// Mirrors the server's MAX_UPLOAD_BYTES default so an oversized file is
// rejected instantly instead of after a pointless upload. The SERVER remains
// authoritative: if the two ever disagree, the request goes through and the
// real 413 is what the user sees.
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

// ChatRequest.history caps at 50 messages and rejects more with 422. Trimming
// here keeps a long conversation working instead of failing at message 51.
const MAX_HISTORY_MESSAGES = 50;

// Copy for every code app/errors.py can return, plus the framework's own
// validation code. `message` from the server is the fallback for anything not
// listed, so a new server-side error still shows something useful.
const ERROR_COPY = {
  NOT_A_PDF: "That file isn't a PDF. Check the file and try again.",
  FILE_TOO_LARGE: "That file is too large — the limit is 20 MB.",
  PDF_UNREADABLE: "Couldn't read that PDF. It may be corrupt or password-protected.",
  NO_TEXT_EXTRACTED:
    "No text found in that PDF. Scanned or image-only documents aren't supported.",
  EMBEDDING_FAILED: "The embedding service is unavailable right now. Try again in a moment.",
  ANSWER_FAILED:
    "Couldn't generate an answer — the model is rate limited or unavailable.",
  DOCUMENT_NOT_FOUND: "That document no longer exists. Pick another one.",
  INTERNAL_ERROR: "Something went wrong on the server.",
};

const state = {
  view: "upload",
  documents: [],
  selectedId: null,
  // [{ role, content }], plus citations + citationBasis on assistant turns.
  messages: [], // page state only
  busy: false,
};

const el = {
  tabUpload: document.getElementById("tab-upload"),
  tabChat: document.getElementById("tab-chat"),
  viewUpload: document.getElementById("view-upload"),
  viewChat: document.getElementById("view-chat"),
  dropzone: document.getElementById("dropzone"),
  fileInput: document.getElementById("file-input"),
  uploadStatus: document.getElementById("upload-status"),
  picker: document.getElementById("doc-picker"),
  clearChat: document.getElementById("clear-chat"),
  transcript: document.getElementById("transcript"),
  chatForm: document.getElementById("chat-form"),
  question: document.getElementById("question"),
  send: document.getElementById("send"),
};

/* ------------------------------- helpers -------------------------------- */

/** Build an element. `text` is set via textContent — never innerHTML, since
 *  filenames, model answers and server messages all reach the DOM. */
function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function clear(node) {
  node.replaceChildren();
}

/** Pull a readable message out of any failure: the error envelope when the
 *  body is the JSON we expect, the status line when it is not (a proxy error,
 *  say), or a network message when the request never arrived. */
function describeFailure(status, bodyText) {
  try {
    const parsed = JSON.parse(bodyText);
    const error = parsed && parsed.error;
    if (error && error.code) {
      return { code: error.code, message: ERROR_COPY[error.code] || error.message };
    }
  } catch {
    // Not JSON — fall through to the status-based message below.
  }
  return {
    code: "UNKNOWN",
    message: status
      ? `Request failed (HTTP ${status}).`
      : "Couldn't reach the server. Is it still running?",
  };
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

/* -------------------------------- views --------------------------------- */

function showView(name) {
  state.view = name;
  const onUpload = name === "upload";

  el.viewUpload.hidden = !onUpload;
  el.viewChat.hidden = onUpload;
  el.tabUpload.setAttribute("aria-selected", String(onUpload));
  el.tabChat.setAttribute("aria-selected", String(!onUpload));

  // Refetch on entry so a document uploaded since the last visit appears.
  // `state.messages` is untouched, which is what makes the conversation
  // survive a trip to the upload screen and back.
  if (!onUpload) {
    loadDocuments();
    el.question.focus();
  }
}

el.tabUpload.addEventListener("click", () => showView("upload"));
el.tabChat.addEventListener("click", () => showView("chat"));

/* ==============================  UPLOAD  =============================== */

// Both drag-and-drop and Browse Files funnel into startUpload().
el.fileInput.addEventListener("change", () => {
  const file = el.fileInput.files[0];
  // Reset first: picking the same file twice fires no `change` event otherwise.
  el.fileInput.value = "";
  if (file) startUpload(file);
});

// dragover must be prevented on every event or the browser navigates away to
// open the dropped file instead of handing it to us.
["dragenter", "dragover"].forEach((type) =>
  el.dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    el.dropzone.classList.add("is-dragging");
  })
);

["dragleave", "dragend"].forEach((type) =>
  el.dropzone.addEventListener(type, () => el.dropzone.classList.remove("is-dragging"))
);

el.dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  el.dropzone.classList.remove("is-dragging");
  const file = event.dataTransfer && event.dataTransfer.files[0];
  if (file) startUpload(file);
});

function setUploadBusy(busy) {
  state.busy = busy;
  el.dropzone.classList.toggle("is-busy", busy);
}

function startUpload(file) {
  if (state.busy) return;

  // Client-side pre-check. Cheap instant feedback; the server still validates
  // the bytes properly, including the %PDF- header we cannot check here.
  if (file.size === 0) {
    renderUploadError("That file is empty.");
    return;
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    const mb = (file.size / (1024 * 1024)).toFixed(1);
    renderUploadError(`That file is ${mb} MB — the limit is 20 MB.`);
    return;
  }

  setUploadBusy(true);
  const bar = renderUploadProgress(file.name);

  // XMLHttpRequest, not fetch: fetch cannot report request-body progress, and
  // `upload.onprogress` is the only way to show real bytes-sent.
  const body = new FormData();
  body.append("file", file); // field name must match `file: UploadFile` server-side

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/documents");

  xhr.upload.addEventListener("progress", (event) => {
    if (!event.lengthComputable) return;
    bar.setPercent(Math.round((event.loaded / event.total) * 100));
  });

  // Bytes are all sent; the server is now parsing the PDF and calling Gemini.
  // That phase reports nothing, so the bar switches to indeterminate rather
  // than sitting at 100% looking hung.
  xhr.upload.addEventListener("load", () => bar.setProcessing());

  xhr.addEventListener("load", () => {
    setUploadBusy(false);
    if (xhr.status === 201) {
      let doc;
      try {
        doc = JSON.parse(xhr.responseText);
      } catch {
        renderUploadError("The server returned a response we couldn't read.");
        return;
      }
      renderUploadSuccess(doc);
    } else {
      renderUploadError(describeFailure(xhr.status, xhr.responseText).message);
    }
  });

  xhr.addEventListener("error", () => {
    setUploadBusy(false);
    renderUploadError("Couldn't reach the server. Is it still running?");
  });

  xhr.addEventListener("abort", () => {
    setUploadBusy(false);
    renderUploadError("The upload was cancelled.");
  });

  xhr.send(body);
}

/** Draw the progress card and return handles for its two phases. */
function renderUploadProgress(filename) {
  clear(el.uploadStatus);

  const card = make("div", "card");
  card.append(make("p", "card-file", filename));
  const label = make("p", "bar-label", "Uploading… 0%");
  const bar = make("div", "bar");
  const fill = make("div", "bar-fill");
  bar.append(fill);
  card.append(label, bar);
  el.uploadStatus.append(card);

  return {
    setPercent(percent) {
      fill.style.width = `${percent}%`;
      label.textContent = `Uploading… ${percent}%`;
    },
    setProcessing() {
      bar.classList.add("is-indeterminate");
      fill.style.width = "";
      label.textContent = "Extracting text and embedding… this can take a while.";
    },
  };
}

function renderUploadSuccess(doc) {
  clear(el.uploadStatus);

  const card = make("div", "card card-ok");
  card.append(make("p", "card-title", "Uploaded and indexed"));
  card.append(make("p", "card-file", doc.filename));
  card.append(
    make(
      "p",
      "card-meta",
      `${plural(doc.page_count, "page")} · ${plural(doc.chunk_count, "chunk")} embedded`
    )
  );

  const go = make("button", "btn btn-primary", "Ask questions about this");
  go.type = "button";
  go.addEventListener("click", () => {
    // A different document means the existing transcript no longer applies.
    if (state.selectedId && state.selectedId !== doc.document_id) {
      state.messages = [];
    }
    state.selectedId = doc.document_id;
    showView("chat");
  });
  card.append(go);

  el.uploadStatus.append(card);
}

function renderUploadError(message) {
  clear(el.uploadStatus);
  const card = make("div", "card card-error");
  card.append(make("p", "card-title", "Upload failed"));
  card.append(make("p", null, message));
  el.uploadStatus.append(card);
}

/* ===============================  CHAT  ================================ */

async function loadDocuments() {
  try {
    const response = await fetch("/api/documents");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const body = await response.json();
    state.documents = body.documents || [];
  } catch {
    state.documents = [];
    state.selectedId = null;
    // renderPicker() too, not just the message: without it the picker keeps the
    // options from the last successful load and stays enabled, so the composer
    // would happily send a question against a document id we no longer have.
    renderPicker();
    renderTranscript("Couldn't load your documents. Is the server running?");
    return;
  }

  // Keep the current selection if it still exists, else fall back to the first.
  const stillThere = state.documents.some((d) => d.document_id === state.selectedId);
  if (!stillThere) {
    state.selectedId = state.documents.length ? state.documents[0].document_id : null;
  }

  renderPicker();
  renderTranscript();
}

function renderPicker() {
  clear(el.picker);
  for (const doc of state.documents) {
    const option = make(
      "option",
      null,
      `${doc.filename} — ${plural(doc.page_count, "page")}`
    );
    option.value = doc.document_id;
    el.picker.append(option);
  }
  el.picker.value = state.selectedId || "";

  const none = state.documents.length === 0;
  el.picker.disabled = none;
  el.question.disabled = none || state.busy;
  el.send.disabled = none || state.busy;
}

el.picker.addEventListener("change", () => {
  // Switching documents starts a new conversation: history from one PDF is
  // meaningless against another, and phase 1 chats with one document at a time.
  state.selectedId = el.picker.value;
  state.messages = [];
  renderTranscript();
  el.question.focus();
});

el.clearChat.addEventListener("click", () => {
  state.messages = [];
  renderTranscript();
  el.question.focus();
});

/** Rewrite the transcript from `state.messages`. Pass `loadError` to replace
 *  the whole transcript with a failure row — used when the document list
 *  itself could not be fetched, so there is nothing to converse about. */
function renderTranscript(loadError) {
  clear(el.transcript);

  if (loadError) {
    el.transcript.append(make("div", "row-error", loadError));
    return;
  }

  if (state.documents.length === 0) {
    el.transcript.append(
      make("p", "empty", "No documents yet. Upload a PDF on the Upload screen to begin.")
    );
    return;
  }

  if (state.messages.length === 0) {
    el.transcript.append(
      make("p", "empty", "Ask a question about this document to get started.")
    );
  }

  for (const message of state.messages) {
    const kind = message.role === "user" ? "bubble-user" : "bubble-assistant";
    el.transcript.append(make("div", `bubble ${kind}`, message.content));
    const sources = citationRow(message);
    if (sources) el.transcript.append(sources);
  }

  if (state.busy) {
    el.transcript.append(make("div", "bubble bubble-assistant bubble-pending", "Thinking…"));
  }

  el.transcript.scrollTop = el.transcript.scrollHeight;
}

/** Page references under an answer, or null when there are none.
 *
 *  The label is not cosmetic. "Cited" means the model named these pages as the
 *  ones it used; "Retrieved" means it did not, so they are only what the search
 *  returned and the answer may rest on none of them. Presenting the second as
 *  the first would be the app vouching for something it cannot check. */
function citationRow(message) {
  if (!message.citations || message.citations.length === 0) return null;

  const cited = message.citationBasis === "cited";
  const row = make("div", "citations");

  const label = make("span", "citations-label", cited ? "Cited" : "Retrieved");
  label.title = cited
    ? "Pages the model said it used to answer."
    : "The model did not mark its sources. These are the pages that were searched.";
  row.append(label);

  for (const citation of message.citations) {
    const chip = make("span", "citation", `Page No. ${citation.page}`);
    // The snippet is the evidence; a tooltip keeps it out of the way until
    // someone wants to check the page against what was actually read.
    chip.title = citation.snippet;
    row.append(chip);
  }

  return row;
}

function appendErrorRow(message, retry) {
  const row = make("div", "row-error");
  row.append(make("span", null, message));
  if (retry) {
    const button = make("button", "btn btn-quiet", "Retry");
    button.type = "button";
    button.addEventListener("click", () => {
      row.remove();
      retry();
    });
    row.append(button);
  }
  el.transcript.append(row);
  el.transcript.scrollTop = el.transcript.scrollHeight;
}

// Grow the textarea to fit, up to the CSS max-height. Enter sends; Shift+Enter
// inserts a newline, the convention people expect from a chat box.
function autoGrow() {
  el.question.style.height = "auto";
  el.question.style.height = `${el.question.scrollHeight}px`;
}

el.question.addEventListener("input", autoGrow);

el.question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    el.chatForm.requestSubmit();
  }
});

el.chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = el.question.value.trim();
  if (!question || state.busy || !state.selectedId) return;

  el.question.value = "";
  autoGrow();
  ask(question);
});

async function ask(question) {
  // Snapshot the history BEFORE adding this question. The server puts history
  // and the question into the prompt separately, so including it in both would
  // send it twice.
  // Mapped down to role and content: assistant turns also carry citations in
  // page state, and replaying those to the server would send snippets it
  // already has back into the prompt's token budget.
  const history = state.messages
    .slice(-MAX_HISTORY_MESSAGES)
    .map(({ role, content }) => ({ role, content }));
  const documentId = state.selectedId;

  state.messages.push({ role: "user", content: question });
  state.busy = true;
  renderPicker();
  renderTranscript();

  let response;
  let bodyText;
  try {
    response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document_id: documentId, question, history }),
    });
    bodyText = await response.text();
  } catch {
    state.busy = false;
    renderPicker();
    renderTranscript();
    appendErrorRow("Couldn't reach the server. Is it still running?", () => retry(question));
    return;
  }

  state.busy = false;
  renderPicker();

  if (response.ok) {
    let payload;
    try {
      payload = JSON.parse(bodyText);
    } catch {
      renderTranscript();
      appendErrorRow("The server returned a response we couldn't read.");
      return;
    }
    state.messages.push({
      role: "assistant",
      content: payload.answer,
      citations: payload.citations ?? [],
      citationBasis: payload.citation_basis ?? "retrieved",
    });
    renderTranscript();
    el.question.focus();
    return;
  }

  const failure = describeFailure(response.status, bodyText);
  renderTranscript();

  // ANSWER_FAILED is usually a transient rate limit, so it gets a real retry
  // rather than a dead end. A 404 means the document is gone — reload the list
  // instead, since retrying the same id cannot succeed.
  if (failure.code === "DOCUMENT_NOT_FOUND") {
    appendErrorRow(failure.message);
    loadDocuments();
    return;
  }
  appendErrorRow(failure.message, () => retry(question));
}

/** Re-ask a question after a failure, dropping the user bubble the failed
 *  attempt left behind so the transcript does not accumulate duplicates. */
function retry(question) {
  const last = state.messages[state.messages.length - 1];
  if (last && last.role === "user" && last.content === question) {
    state.messages.pop();
  }
  ask(question);
}

/* -------------------------------- startup ------------------------------- */

showView("upload");
