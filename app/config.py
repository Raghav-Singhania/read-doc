"""Application configuration.

Every setting is read from the environment (or `.env`) exactly once, when this
module is first imported. Importing `settings` from here therefore fails loudly
at startup on bad config, rather than halfway through a user's first upload.
"""

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Secrets ---
    # No default, so a missing GEMINI_API_KEY is a startup error.
    gemini_api_key: str = Field(min_length=1)

    # --- Storage ---
    storage_dir: Path = Path("storage/documents")
    chroma_dir: Path = Path("storage/chroma")

    # --- Chunking ---
    chunk_size: int = Field(default=1000, gt=0)
    chunk_overlap: int = Field(default=200, ge=0)

    # --- Retrieval ---
    retrieval_k: int = Field(default=4, gt=0)

    # --- Uploads ---
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, gt=0)

    @model_validator(mode="after")
    def _overlap_fits_inside_chunk(self) -> "Settings":
        # An overlap >= chunk size makes RecursiveCharacterTextSplitter loop
        # or emit near-duplicate chunks, which quietly wrecks retrieval.
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}) must be smaller than "
                f"chunk_size ({self.chunk_size})"
            )
        return self


# Module-level instantiation is deliberate: it is what makes validation happen
# at startup. A lazy `@lru_cache get_settings()` would defer the failure to the
# first request that happened to need config.
settings = Settings()
