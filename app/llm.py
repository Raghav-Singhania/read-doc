"""The chat model, behind one factory.

Sits beside `embeddings.py` and mirrors it: one place naming the model, one seam
for tests to stub.
"""

from langchain_core.language_models import BaseChatModel

from app.config import settings

# Safe to change, unlike EMBEDDING_MODEL.
#
# Swapping the generation model re-reads the same stored vectors and needs no
# re-ingest, so this is a normal configuration decision rather than a one-way
# door. Verify a replacement against the three cases in
# `app/rag/prompts/answer_v1.py` before shipping it — grounding, declining, and
# ignoring instructions embedded in the document.
#
# Chosen over `gemini-3.1-flash-lite`, which obeyed an "ignore all previous
# instructions" line planted in the context on 3 of 3 runs. Chosen over
# `gemini-3.6-flash`, which ignores `temperature` (it warns and uses fixed
# sampling defaults), and over `gemini-2.5-flash`, which the API now refuses for
# new keys. Pinned to an exact version rather than the `gemini-flash-latest`
# alias, so the answer behaviour cannot change under us without a commit.
CHAT_MODEL = "gemini-3.5-flash"

# Grounded extraction, not creative writing: we want the same answer twice for
# the same document and question, and section 6's tests depend on it.
TEMPERATURE = 0.0

# A generation call runs inside a request, on a threadpool worker. Without a
# timeout a hung call holds that worker until the client gives up, so a handful
# of them would starve the pool while the app still looks healthy.
#
# `max_retries` counts ATTEMPTS, not retries: the library's own docs say
# `max_retries=1` means "only the initial request, no retries", and `0` means
# "use the Google SDK default" rather than "none". The default is 6, which is
# far too many to sit through inside a synchronous request.
#
# 3 attempts because Gemini returns a transient `503 UNAVAILABLE` ("experiencing
# high demand") often enough to see it in ordinary use — observed repeatedly
# while verifying this section. Retrying absorbs that instead of showing the
# user a 502 for a fault that clears in a second. The timeout is per attempt,
# so this bounds a worst case at roughly a minute against a typical answer of
# 2-7 seconds.
TIMEOUT_SECONDS = 20
MAX_RETRIES = 3


def build_llm() -> BaseChatModel:
    """A client for the chat model. Fresh per call, as with `build_embeddings`."""
    # Lazy import: keeps the google client off the import path of tests that
    # stub the model.
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=CHAT_MODEL,
        google_api_key=settings.gemini_api_key,
        temperature=TEMPERATURE,
        timeout=TIMEOUT_SECONDS,
        max_retries=MAX_RETRIES,
    )
