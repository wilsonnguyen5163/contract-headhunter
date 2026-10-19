"""Local CPU sentence-transformers wrapper. Adds the E5 prefixes and records model identity."""

from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal, Protocol

import numpy as np
from pydantic import BaseModel

from jobmatch.config import EmbeddingSettings

TextKind = Literal["job", "facet"]


class _SentenceModel(Protocol):
    def encode(self, sentences: list[str], **kwargs: Any) -> Any: ...


class EmbeddingInfo(BaseModel):
    model_name: str
    model_version: str
    dim: int


class Encoder:
    """Loads the model lazily (first use may download it into the Hugging Face cache)."""

    def __init__(self, settings: EmbeddingSettings, model: _SentenceModel | None = None) -> None:
        self._settings = settings
        self._model = model
        self._info: EmbeddingInfo | None = None

    def _load(self) -> _SentenceModel:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # heavy import (torch)

            model = SentenceTransformer(
                self._settings.model_name,
                device="cpu",  # never use a GPU: the client machine has none
                revision=self._settings.model_revision,
            )
            model.max_seq_length = self._settings.max_seq_length
            self._model = model
        return self._model

    def info(self) -> EmbeddingInfo:
        if self._info is None:
            dim = int(self._raw_encode(["probe"]).shape[1])
            self._info = EmbeddingInfo(
                model_name=self._settings.model_name,
                model_version=_model_version(self._settings.model_revision),
                dim=dim,
            )
        return self._info

    def encode(self, texts: Sequence[str], kind: TextKind) -> np.ndarray:
        prefix = self._settings.job_prefix if kind == "job" else self._settings.facet_prefix
        return self._raw_encode([prefix + t for t in texts])

    def _raw_encode(self, texts: list[str]) -> np.ndarray:
        vectors = self._load().encode(
            texts,
            batch_size=self._settings.batch_size,
            normalize_embeddings=self._settings.normalize_embeddings,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)


def _model_version(revision: str | None) -> str:
    try:
        st_version = version("sentence-transformers")
    except PackageNotFoundError:
        st_version = "unknown"
    return f"{revision or 'unpinned'}+sentence-transformers-{st_version}"
