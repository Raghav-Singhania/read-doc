"""Versioned prompts that ship in the product.

Importers take `ANSWER_SYSTEM_PROMPT` from here, not from a version module, so
promoting a new version is a one-line change in this file and the chain does
not name a version at all.
"""

from app.rag.prompts.answer_v1 import ANSWER_SYSTEM_PROMPT, VERSION as ANSWER_VERSION

__all__ = ["ANSWER_SYSTEM_PROMPT", "ANSWER_VERSION"]
