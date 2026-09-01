"""Splitting pages into embeddable chunks.

Chunk metadata is built from scratch rather than inherited from the loader. The
loader attaches `file_path` (an absolute path on this machine) and a 0-indexed
`page`; the first leaks server layout into a store the API reads back to the
client, and the second would show up as "page 0" in any later citation. Chroma
also only accepts str/int/float/bool values, so passing loader metadata through
is a latent crash as well as a leak.
"""

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import settings


def build_splitter() -> RecursiveCharacterTextSplitter:
    """Split on paragraph, then line, then word boundaries before resorting to
    mid-word cuts — the default separator ladder, which keeps sentences intact
    far more often than a fixed-width split would."""
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        length_function=len,
    )


def chunk_pages(
    pages: list[Document], *, document_id: str, filename: str
) -> list[Document]:
    """Split pages into chunks tagged with `document_id`, `filename`, `page`
    and `ordinal`.

    `ordinal` counts across the whole document, not per page, because it is half
    of the vector-store id (`{document_id}:{ordinal}`) and so has to be unique
    within the document.
    """
    splitter = build_splitter()
    chunks: list[Document] = []

    for page in pages:
        # 0-indexed from the loader; humans count pages from 1.
        page_number = int(page.metadata.get("page", 0)) + 1

        for text in splitter.split_text(page.page_content):
            if not text.strip():
                continue
            chunks.append(
                Document(
                    page_content=text,
                    metadata={
                        "document_id": document_id,
                        "filename": filename,
                        "page": page_number,
                        "ordinal": len(chunks),
                    },
                )
            )

    return chunks


def chunk_id(chunk: Document) -> str:
    """The vector-store id for a chunk.

    Deterministic on purpose: re-ingesting the same document overwrites its
    existing vectors instead of accumulating a second copy of every chunk.
    """
    return f"{chunk.metadata['document_id']}:{chunk.metadata['ordinal']}"
