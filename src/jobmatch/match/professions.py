"""Profession notes for reviewers: "profession: recruiting · talent: sales (different)".

Each job gets the profession (config/professions.yaml) closest to its core text (title and
first responsibilities); each contractor gets the professions of their titles (headline and
target titles). A title counts only when its best profession beats the second by
min_margin, so generic titles ("Consultant") say nothing. Verdicts: same, close (a pair
listed under `close`), different, or unclear (the job's profession is not clear, or no
contractor title is).

Notes are data shown to the reviewer; they never change a score (2026-11-04: on blind
labels a mismatch penalty cleaned shortlists but did not reliably rank better, see
doc/PROTOTYPES.md). Vectors come from embed-jobs ("job_cores", "professions") and
embed-contractors ("contractor_titles"); when one is missing or stale there are no notes.
"""

from collections.abc import Iterable
from enum import StrEnum

import numpy as np
from pydantic import BaseModel

from jobmatch.config import Professions, Settings, load_normalization_rules, load_professions
from jobmatch.embed.texts import (
    PROFESSION_TEMPLATE_HASH,
    TITLE_TEMPLATE_HASH,
    TitleCleaner,
    content_hash,
    contractor_title_items,
    job_core_text,
    profession_text,
    title_owner,
)
from jobmatch.schemas import ContractorProfile, Job, VectorSet
from jobmatch.storage.vector_store import NpzVectorStore


class Verdict(StrEnum):
    SAME = "same"
    CLOSE = "close"
    DIFFERENT = "different"
    UNCLEAR = "unclear"


class ProfessionNote(BaseModel):
    job: str | None  # None when the job's profession is not clear
    contractor: list[str]  # the contractor's clear professions, sorted
    verdict: Verdict

    def text(self) -> str:
        talent = ", ".join(self.contractor) or "unclear"
        return f"job: {self.job or 'unclear'} · talent: {talent} ({self.verdict.value})"


def _unit(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1, norms)


class ProfessionFinder:
    """Assigns professions to vectors and compares a job with a contractor."""

    def __init__(self, professions: Professions, vectors: np.ndarray) -> None:
        self.names = list(professions.professions)
        if len(vectors) != len(self.names):
            raise ValueError("one vector per profession expected")
        self._vectors = _unit(np.asarray(vectors, dtype=np.float32))
        self._min_margin = professions.min_margin
        self._close = {frozenset((a, b)) for g in professions.close for a in g for b in g if a != b}

    def assign(self, vectors: np.ndarray) -> list[str | None]:
        """The closest profession of each row, or None when the second is within min_margin."""
        if len(vectors) == 0:
            return []
        sims = _unit(np.asarray(vectors, dtype=np.float32)) @ self._vectors.T
        order = np.argsort(-sims, axis=1)
        out: list[str | None] = []
        for row, (best, second) in zip(sims, order[:, :2], strict=True):
            clear = row[best] - row[second] >= self._min_margin
            out.append(self.names[best] if clear else None)
        return out

    def compare(self, job: str | None, contractor: Iterable[str | None]) -> ProfessionNote:
        mine = sorted({p for p in contractor if p})
        if job is None or not mine:
            verdict = Verdict.UNCLEAR
        elif job in mine:
            verdict = Verdict.SAME
        elif any(frozenset((job, p)) in self._close for p in mine):
            verdict = Verdict.CLOSE
        else:
            verdict = Verdict.DIFFERENT
        return ProfessionNote(job=job, contractor=mine, verdict=verdict)


def _current(vs: VectorSet, texts: dict[str, str], template: str, model: str) -> bool:
    """True when every text has a vector made from it, with this template and model."""
    if vs.text_template_hash != template or vs.model_name != model:
        return False
    hashes = dict(zip(vs.ids, vs.content_hashes, strict=True))
    return all(hashes.get(i) == content_hash(t) for i, t in texts.items())


def _rows(vs: VectorSet, ids: Iterable[str]) -> np.ndarray:
    index = {item_id: row for row, item_id in enumerate(vs.ids)}
    return vs.vectors[[index[i] for i in ids]]


def profession_notes(
    settings: Settings, jobs: Iterable[Job], profiles: Iterable[ContractorProfile]
) -> tuple[dict[tuple[str, str], ProfessionNote], str | None]:
    """A note for every (job_id, talent_id) pair, or no notes and the reason why."""
    professions = load_professions(settings.config_dir)
    if professions is None:
        return {}, "no config/professions.yaml"
    jobs, profiles = list(jobs), list(profiles)
    rules = load_normalization_rules(settings.config_dir)
    cleaner = TitleCleaner(rules.seniority, rules.title_engagement_words)
    store = NpzVectorStore(
        settings.paths.data_dir / "embeddings", lock_timeout=settings.storage.lock_timeout_seconds
    )
    model = settings.embedding.model_name
    prof_texts = {n: profession_text(n, d) for n, d in professions.professions.items()}
    core_texts = {j.job_id: job_core_text(j) for j in jobs}
    title_texts = dict(item for p in profiles for item in contractor_title_items(p, cleaner))
    prof_vs, core_vs = store.load("professions"), store.load("job_cores")
    title_vs = store.load("contractor_titles")
    if (
        prof_vs is None
        or core_vs is None
        or not _current(prof_vs, prof_texts, PROFESSION_TEMPLATE_HASH, model)
        or not _current(core_vs, core_texts, PROFESSION_TEMPLATE_HASH, model)
    ):
        return {}, "profession vectors missing or out of date: run embed-jobs"
    if title_vs is None or not _current(title_vs, title_texts, TITLE_TEMPLATE_HASH, model):
        return {}, "contractor title vectors missing or out of date: run embed-contractors"
    if len({prof_vs.model_version, core_vs.model_version, title_vs.model_version}) > 1:
        return {}, "vectors from different model versions: run embed-contractors and embed-jobs"

    finder = ProfessionFinder(professions, _rows(prof_vs, prof_texts))
    job_prof = dict(zip(core_texts, finder.assign(_rows(core_vs, core_texts)), strict=True))
    by_contractor: dict[str, list[str | None]] = {p.talent_id: [] for p in profiles}
    for item_id, prof in zip(title_texts, finder.assign(_rows(title_vs, title_texts)), strict=True):
        by_contractor[title_owner(item_id)].append(prof)
    return {
        (j, c): finder.compare(job_prof[j], titles)
        for j in core_texts
        for c, titles in by_contractor.items()
    }, None
