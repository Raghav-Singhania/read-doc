"""Answer generation: retrieve from one document, then answer from what came back.

`answer_question` is the whole public surface. `POST /api/chat` calls it and
nothing else, which is what keeps a streaming endpoint later a second thin
wrapper over this function rather than a rewrite.

`Turn` is defined here rather than imported from `app.api.schemas` on purpose.
The service must not depend on the web layer — that direction of import is what
makes this callable from a worker, a test, or a CLI.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from langchain_classic.chains import create_retrieval_chain
from langchain_classic.chains.combine_documents import create_stuff_documents_chain
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores import VectorStore

from app.config import settings
from app.errors import AnswerGenerationError, AppError
from app.llm import build_llm
from app.rag.prompts import ANSWER_SYSTEM_PROMPT


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
    retriever = _build_retriever(document_id, vector_store=vector_store)
    combine_docs = create_stuff_documents_chain(llm or build_llm(), ANSWER_PROMPT)
    chain = create_retrieval_chain(retriever, combine_docs)

    try:
        # `input` is what the retriever embeds and searches on — the raw
        # question, deliberately not rewritten against the history. Condensing
        # is out of scope for phase 1, and a condensing step that misfires
        # silently retrieves the wrong chunks.
        #
        # `chat_history` reaches the prompt but never the retriever, so a
        # follow-up reads as conversational without the history distorting what
        # gets retrieved.
        result = chain.invoke(
            {"input": question, "chat_history": _as_messages(history)}
        )
    except AppError:
        raise
    except Exception as exc:
        # Covers both upstream steps — Chroma's similarity search and the
        # Gemini call. Neither is the user's fault, so both are 502. They share
        # one code because the client's recourse is the same either way: retry.
        raise AnswerGenerationError(
            "Could not generate an answer. The model may be unavailable or "
            "rate limited. Try again in a moment."
        ) from exc

    # `create_retrieval_chain` returns `input`, `chat_history`, `context` and
    # `answer`. Only the answer goes to the client in phase 1 — `context` is
    # what citations would be built from in a later phase.
    return str(result["answer"]).strip()


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
