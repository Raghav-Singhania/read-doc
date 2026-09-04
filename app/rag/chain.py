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
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
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
) -> str:
    """Answer `question` about one document, given prior `history`.

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
    combine_docs = create_stuff_documents_chain(model, ANSWER_PROMPT)
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
        raise AnswerGenerationError(
            "Could not generate an answer. The model may be unavailable or "
            "rate limited. Try again in a moment."
        ) from exc

    # `create_retrieval_chain` returns `input`, `chat_history`, `context` and
    # `answer`. Only the answer goes to the client in phase 1 — `context` is
    # what citations would be built from in a later phase.
    return str(result["answer"]).strip()


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

    The logging tap sits between the rewrite and the retriever, so what gets
    recorded is the exact string that was embedded and searched.
    """
    tapped = RunnableLambda(
        partial(_log_rewrite, original=question), name="log_condensed_question"
    ) | _build_retriever(document_id, vector_store=vector_store)

    return create_history_aware_retriever(model, tapped, CONDENSE_PROMPT)


def _log_rewrite(query: str, *, original: str) -> str:
    """Record a rewritten question on its way to the retriever, unchanged.

    Phase 2's plan asks for this because a condensing step that misfires
    retrieves the wrong chunks *silently*: the answer still reads well, and
    without the rewrite recorded there is nothing to look at afterwards.

    Only a changed question is logged. The tap runs on both of the library's
    branches, and on the no-history branch `query` is the original — as it also
    is when the model correctly decides a standalone question needs no rewrite.
    Neither is worth a line, and neither can be told from the other here.
    """
    if query != original:
        logger.info("Condensed question for retrieval: %r -> %r", original, query)
    return query


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
