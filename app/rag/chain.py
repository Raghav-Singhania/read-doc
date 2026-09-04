"""Answer generation: condense the question, retrieve from one document, then
answer from what came back.

`answer_question` is the whole public surface. `POST /api/chat` calls it and
nothing else, which is what keeps a streaming endpoint later a second thin
wrapper over this function rather than a rewrite.

`Turn` is defined here rather than imported from `app.api.schemas` on purpose.
The service must not depend on the web layer — that direction of import is what
makes this callable from a worker, a test, or a CLI.
"""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from typing import Literal

from langchain_classic.chains import (
    create_history_aware_retriever,
    create_retrieval_chain,
)
from langchain_classic.chains.combine_documents import create_stuff_documents_chain
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.documents import Document
from langchain_core.prompts import (
    ChatPromptTemplate,
    MessagesPlaceholder,
    PromptTemplate,
)
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.vectorstores import VectorStore

from app.config import settings
from app.errors import AnswerGenerationError, AppError
from app.llm import build_llm
from app.rag.prompts import ANSWER_SYSTEM_PROMPT, CONDENSE_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Turn:
    """One message of prior conversation, as the client replayed it.

    Phase 1 keeps history on the client, so every request carries the whole
    conversation and the server stores none of it.
    """

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class Source:
    """One page the answer drew on, or was at least retrieved from.

    `snippet` is trimmed here rather than on the wire: the full chunk is up to
    `CHUNK_SIZE` characters of raw PDF text, and the client only ever renders a
    line of it under the answer.
    """

    document_id: str
    filename: str
    page: int
    ordinal: int
    snippet: str


@dataclass(frozen=True)
class Answer:
    """An answer and where it came from.

    `basis` is the honest part. "cited" means the model marked these pages as
    the ones it used. "retrieved" means it did not, so these are simply the
    chunks similarity search returned — which the answer may or may not rest
    on. Collapsing the two would turn a guess into a claim.
    """

    text: str
    sources: tuple[Source, ...]
    basis: Literal["cited", "retrieved"]


# How much of a chunk reaches the client under an answer.
SNIPPET_CHARS = 240

# The trailing line answer_v2 asks for: "SOURCES: 4, 7". Case-insensitive and
# tolerant of stray whitespace, because this is model output — but anchored to
# the start of the line so a sentence merely mentioning sources is not eaten.
SOURCES_MARKER = re.compile(r"^\s*SOURCES\s*:(?P<pages>.*)$", re.IGNORECASE)


# Prefixes every excerpt with its page before the model sees it. Without this
# the model has no way to name a source: the chunks arrive as bare text, and
# `page` sits in metadata the prompt never renders.
EXCERPT_PROMPT = PromptTemplate.from_template("[page {page}]\n{page_content}")


# `chat_history` sits between the rules and the question: the model reads its
# instructions, then the conversation so far, then what is being asked now.
# `optional=True` because `create_retrieval_chain` supplies an empty list when
# the key is absent, and a required placeholder would reject that.
ANSWER_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", ANSWER_SYSTEM_PROMPT),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
    ]
)

# Same shape, different job: this one produces a search query, not an answer.
#
# `chat_history` is required here, unlike above. This prompt is only ever
# rendered on the branch that has history — see `_condensing_retriever` — and a
# condense prompt reached with an empty conversation would be a bug worth
# failing on rather than quietly rewriting a question against nothing.
CONDENSE_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", CONDENSE_SYSTEM_PROMPT),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
    ]
)


def answer_question(
    document_id: str,
    question: str,
    history: Sequence[Turn] = (),
    *,
    llm: BaseChatModel | None = None,
    vector_store: VectorStore | None = None,
) -> Answer:
    """Answer `question` about one document, with the pages it came from.

    `llm` and `vector_store` are injectable so section 6 can run this with a
    fake model and a fake store — no network, no API key, deterministic.

    The caller has already checked that `document_id` exists, so this does not
    repeat that lookup.
    """
    # One model instance for both jobs — condensing and answering.
    #
    # A smaller, cheaper model for the condense step is the obvious saving and
    # is deliberately not taken. Condensing reads the same untrusted text the
    # answer step does (prior assistant turns quote the uploaded document), and
    # `answer_v1.py` records `gemini-3.1-flash-lite` obeying an injected
    # "ignore all previous instructions" on 3 of 3 runs. A prompt-injected
    # search query fails more quietly than a prompt-injected answer: the user
    # reads a fluent answer built from the wrong chunks.
    model = llm or build_llm()

    retriever = _condensing_retriever(
        document_id, question, model=model, vector_store=vector_store
    )
    combine_docs = create_stuff_documents_chain(
        model, ANSWER_PROMPT, document_prompt=EXCERPT_PROMPT
    )
    chain = create_retrieval_chain(retriever, combine_docs)

    try:
        # `input` is the user's question, verbatim, and it stays that way:
        # `create_retrieval_chain` assigns `context` and `answer` onto the input
        # dict without replacing `input`, so the condensed query reaches the
        # retriever while the answer prompt still sees what the user actually
        # typed. Answering the rewrite instead would let a condensing slip
        # change the question the user gets an answer to, not just the chunks.
        result = chain.invoke(
            {"input": question, "chat_history": _as_messages(history)}
        )
    except AppError:
        raise
    except Exception as exc:
        # Covers all three upstream steps now — the condense call, Chroma's
        # similarity search, and the answer call. None is the user's fault, so
        # all are 502, and they share one code because the client's recourse is
        # the same either way: retry.
        #
        # Logged for the same reason as the ingest path: one shared code is
        # right for the client and useless for debugging, and these three steps
        # fail for entirely different reasons.
        logger.exception("Answering failed for document %s", document_id)
        raise AnswerGenerationError(
            "Could not generate an answer. The model may be unavailable or "
            "rate limited. Try again in a moment."
        ) from exc

    # `create_retrieval_chain` returns `input`, `chat_history`, `context` and
    # `answer`. `context` is the retrieved chunks, and it is what the citations
    # are built from — phase 1 discarded it here.
    text, marked = _split_sources_marker(str(result["answer"]))
    sources, basis = _collect_sources(result.get("context") or [], marked)
    return Answer(text=text, sources=sources, basis=basis)


def _split_sources_marker(answer: str) -> tuple[str, tuple[int, ...] | None]:
    """Separate the trailing `SOURCES:` line from the answer the user reads.

    Returns the answer without that line, and the pages it named — where
    `None` and `()` mean different things and the caller depends on the
    difference: `None` is no marker at all (the model ignored the instruction,
    so nothing can be called cited), while `()` is the model explicitly saying
    it used no excerpt, which is what a refusal should produce.

    Only the last non-empty line is considered. A model that writes about
    sources mid-answer does not get that prose treated as a marker.
    """
    lines = answer.rstrip().splitlines()
    if not lines:
        return "", None

    match = SOURCES_MARKER.match(lines[-1])
    if match is None:
        return answer.strip(), None

    # `\d+` rather than splitting on commas: the separator the model actually
    # uses varies ("4, 7", "4 and 7", "p4; p7") and every one of those yields
    # the same page numbers this way.
    pages = tuple(int(n) for n in re.findall(r"\d+", match.group("pages")))
    text = "\n".join(lines[:-1]).strip()

    # A reply that is nothing but the marker leaves no answer to show. Treat it
    # as unmarked rather than handing the client an empty bubble: the raw line
    # is poor output, but a blank assistant message reads as a broken app.
    if not text:
        return answer.strip(), None

    return text, pages


def _collect_sources(
    docs: Sequence[Document], marked: tuple[int, ...] | None
) -> tuple[tuple[Source, ...], Literal["cited", "retrieved"]]:
    """Turn retrieved chunks into one source per page.

    Deduped by page because two chunks from one page are one citation to a
    reader. The first chunk for a page wins, and retrieval hands them over in
    similarity order, so the snippet shown is the closest match on that page.

    A page the model marked but that was never retrieved is dropped. The prompt
    forbids inventing one, but a fabricated page number would otherwise render
    as a real citation, and that is the failure this whole feature is supposed
    to prevent rather than introduce.
    """
    wanted = None if marked is None else set(marked)

    by_page: dict[int, Source] = {}
    for doc in docs:
        page = int(doc.metadata.get("page") or 0)
        if wanted is not None and page not in wanted:
            continue
        if page in by_page:
            continue
        by_page[page] = Source(
            document_id=str(doc.metadata.get("document_id") or ""),
            filename=str(doc.metadata.get("filename") or "document.pdf"),
            page=page,
            ordinal=int(doc.metadata.get("ordinal") or 0),
            snippet=_snippet(doc.page_content),
        )

    if wanted:
        invented = wanted - {int(d.metadata.get("page") or 0) for d in docs}
        if invented:
            # Worth a line: it means the answer prompt is being disregarded,
            # which is invisible from the response once these are dropped.
            logger.info("Model cited unretrieved page(s): %s", sorted(invented))

    # Ascending page order for the reader, not similarity order — "p2, p5"
    # reads as a reference; "p5, p2" reads as a mistake.
    ordered = tuple(by_page[page] for page in sorted(by_page))
    return ordered, "retrieved" if marked is None else "cited"


def _snippet(text: str) -> str:
    """One line of a chunk, short enough to sit under an answer.

    Whitespace is collapsed because PDF text arrives full of hard wraps, and
    the cut falls on a word boundary so the tail is not a half word.
    """
    flat = " ".join(text.split())
    if len(flat) <= SNIPPET_CHARS:
        return flat
    return flat[:SNIPPET_CHARS].rsplit(" ", 1)[0] + "…"


def _condensing_retriever(
    document_id: str,
    question: str,
    *,
    model: BaseChatModel,
    vector_store: VectorStore | None = None,
) -> Runnable:
    """The document retriever, behind a question-rewriting step.

    `create_history_aware_retriever` branches on the history itself: with an
    empty `chat_history` it hands `input` straight to the retriever and the
    condense prompt is never rendered. So a first question costs no extra model
    call, and — more to the point — a question that arrives with no conversation
    to resolve against is never rewritten. That guard is the library's, not
    ours; this function only has to not defeat it.

    The tap sits between the rewrite and the retriever, so what gets recorded
    is the exact string that was embedded and searched — and so the rewrite is
    a plain `str` by the time it reaches retrieval. See
    `_query_for_retrieval` for why that conversion is load-bearing.
    """
    tapped = RunnableLambda(
        partial(_query_for_retrieval, original=question), name="prepare_query"
    ) | _build_retriever(document_id, vector_store=vector_store)

    return create_history_aware_retriever(model, tapped, CONDENSE_PROMPT)


def _query_for_retrieval(query: str, *, original: str) -> str:
    """Log a rewritten question, and hand retrieval a plain `str`.

    THE `str()` IS NOT COSMETIC. `StrOutputParser` returns a `TextAccessor`,
    which is a `str` *subclass*. `google.genai` validates the embed call's
    `contents` against a pydantic smart union whose members include `str` and
    `Content`; a subclass does not match the `str` member, so pydantic matches
    it against `Content` and builds an empty `Content()`. The text is dropped
    with no error, the request goes out as `{"content": {}}`, and the API
    answers 500 INTERNAL with no detail. Every follow-up question failed this
    way — the first question was unaffected because it reaches the retriever as
    the raw `str` from the request body, never through the parser.

    Logging: only a changed question is recorded. The tap runs on both of the
    library's branches, and on the no-history branch `query` is the original —
    as it also is when the model decides a standalone question needs no
    rewrite. Neither is worth a line, and neither can be told from the other
    here.
    """
    if query != original:
        logger.info("Condensed question for retrieval: %r -> %r", original, query)
    return str(query)


def _build_retriever(
    document_id: str, *, vector_store: VectorStore | None = None
) -> BaseRetriever:
    """Top `k` chunks for a query, restricted to one document.

    The filter is the important part. Without it, a question about one PDF would
    retrieve the nearest chunks across every document in the collection, and the
    answer would blend two unrelated files with nothing in the response saying
    so.
    """
    if vector_store is None:
        from app.vectorstore import build_vector_store

        vector_store = build_vector_store()

    return vector_store.as_retriever(
        search_kwargs={
            "k": settings.retrieval_k,
            "filter": {"document_id": document_id},
        }
    )


def _as_messages(history: Sequence[Turn]) -> list[BaseMessage]:
    """Turns into the message objects the prompt template expects."""
    return [
        HumanMessage(turn.content)
        if turn.role == "user"
        else AIMessage(turn.content)
        for turn in history
    ]
