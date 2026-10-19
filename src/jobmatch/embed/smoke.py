"""Smoke check: the embedding model loads on this machine, and English and French job texts
land closer to a related contractor facet than to an unrelated one. Texts are synthetic."""

import sys
from time import perf_counter

import numpy as np
from pydantic import BaseModel

from jobmatch.config import EmbeddingSettings
from jobmatch.embed.encoder import Encoder

SMOKE_JOBS = {
    "job_en": (
        "Title: Senior Python Developer | Skills: python, sql, aws | Domain: banking | "
        "Seniority: senior | Summary: 6-month contract to build data pipelines on AWS."
    ),
    "job_fr": (
        "Title: Développeur Python principal | Skills: python, sql, aws | Domain: banque | "
        "Seniority: senior | Summary: Contrat de 6 mois pour construire des pipelines de "
        "données sur AWS."
    ),
}
SMOKE_FACETS = {
    "facet_related": (
        "Title: Data engineering | Skills: python, sql, aws, etl | Domain: banking, insurance | "
        "Seniority: senior | Summary: Ten years building ETL pipelines on AWS."
    ),
    "facet_unrelated": (
        "Title: Registered nurse | Skills: patient care, triage | Domain: healthcare | "
        "Seniority: intermediate | Summary: Emergency room nursing in a hospital."
    ),
}


class SmokeResult(BaseModel):
    model_name: str
    model_version: str
    dim: int
    load_seconds: float
    encode_seconds: float
    texts_encoded: int
    ms_per_text: float
    peak_rss_mb: float | None
    similarities: dict[str, float]
    passed: bool


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def _peak_rss_mb() -> float | None:
    try:
        import resource
    except ImportError:  # Windows
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kilobytes, macOS bytes.
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def run_smoke(settings: EmbeddingSettings, encoder: Encoder | None = None) -> SmokeResult:
    encoder = encoder or Encoder(settings)
    started = perf_counter()
    info = encoder.info()
    load_seconds = perf_counter() - started

    started = perf_counter()
    jobs = encoder.encode(list(SMOKE_JOBS.values()), kind="job")
    facets = encoder.encode(list(SMOKE_FACETS.values()), kind="facet")
    encode_seconds = perf_counter() - started
    texts = len(SMOKE_JOBS) + len(SMOKE_FACETS)

    similarities = {
        f"{job_key}~{facet_key}": round(_cosine(jobs[i], facets[k]), 4)
        for i, job_key in enumerate(SMOKE_JOBS)
        for k, facet_key in enumerate(SMOKE_FACETS)
    }
    passed = all(
        similarities[f"{job_key}~facet_related"] > similarities[f"{job_key}~facet_unrelated"]
        for job_key in SMOKE_JOBS
    )
    return SmokeResult(
        model_name=info.model_name,
        model_version=info.model_version,
        dim=info.dim,
        load_seconds=round(load_seconds, 3),
        encode_seconds=round(encode_seconds, 3),
        texts_encoded=texts,
        ms_per_text=round(1000 * encode_seconds / texts, 1),
        peak_rss_mb=_peak_rss_mb(),
        similarities=similarities,
        passed=passed,
    )
