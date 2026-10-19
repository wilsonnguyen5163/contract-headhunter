from typing import Any

import numpy as np
import pytest

from jobmatch.config import Settings
from jobmatch.embed.encoder import Encoder
from jobmatch.embed.smoke import run_smoke


class FakeModel:
    """Stands in for SentenceTransformer: records calls, returns fixed-size vectors."""

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def encode(self, sentences: list[str], **kwargs: Any) -> np.ndarray:
        self.calls.append((list(sentences), kwargs))
        return np.ones((len(sentences), self.dim), dtype=np.float64)


def test_prefixes_and_settings_are_applied(settings: Settings) -> None:
    model = FakeModel()
    encoder = Encoder(settings.embedding, model=model)
    vectors = encoder.encode(["Développeur Python", "Data engineer"], kind="job")
    encoder.encode(["Analyse d'affaires"], kind="facet")

    assert vectors.dtype == np.float32
    assert vectors.shape == (2, 4)
    job_texts, kwargs = model.calls[0]
    assert job_texts == ["query: Développeur Python", "query: Data engineer"]
    assert model.calls[1][0] == ["passage: Analyse d'affaires"]
    assert kwargs["batch_size"] == settings.embedding.batch_size
    assert kwargs["normalize_embeddings"] is True


def test_info_reports_model_identity(settings: Settings) -> None:
    info = Encoder(settings.embedding, model=FakeModel(dim=7)).info()
    assert info.model_name == settings.embedding.model_name
    assert info.dim == 7
    assert info.model_version.startswith(f"{settings.embedding.model_revision}+")


@pytest.mark.model
def test_real_model_smoke(settings: Settings) -> None:
    """Loads intfloat/multilingual-e5-small on CPU (downloads it on first run)."""
    result = run_smoke(settings.embedding)
    assert result.dim == 384
    assert result.passed, result.similarities
