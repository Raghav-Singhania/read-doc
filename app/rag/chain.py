"""Answer generation — the seam `POST /api/chat` calls.

Section 4 of `PHASE_1.md` fills in `answer_question`, and it is the only thing
the endpoint calls. So section 4 is an edit to this file alone: build the
retriever and the chain here, and chat starts answering with no change to the
API layer. A streaming endpoint later is a second thin wrapper over the same
function rather than a rewrite.

`Turn` is defined here rather than imported from `app.api.schemas` on purpose.
The service must not depend on the web layer — that direction of import is what
makes the function callable from a worker, a test, or a CLI later.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.errors import AnswerUnavailableError


@dataclass(frozen=True)
class Turn:
    """One message of prior conversation, as the client replayed it.

    Phase 1 keeps history on the client, so every request carries the whole
    conversation and the server stores none of it.
    """

    role: Literal["user", "assistant"]
    content: str


def answer_question(
    document_id: str, question: str, history: Sequence[Turn] = ()
) -> str:
    """Answer `question` about one document, given prior `history`.

    Section 4 replaces the body with: retrieve the top `k` chunks from Chroma
    filtered to `document_id`, stuff them into the versioned answer prompt
    alongside `history`, and return the model's reply.

    The caller has already checked that `document_id` exists, so this does not
    repeat that lookup.
    """
    raise AnswerUnavailableError(
        "Answer generation is not available yet. The retrieval chain lands in "
        "section 4 of phase 1; uploading and listing documents already work."
    )
