"""Dedupe normalized jobs against what is already stored (and within the batch).

Rules, in order, for each incoming job:
1. Same (source, source_id) already stored:
   - the stored one is a stub (needs_details) and the new one has text -> FILLS_STUB
     (the stored row is replaced; its first_seen_at is kept);
   - otherwise -> ALREADY_STORED (nothing written; re-running an import is safe).
2. The same job posted elsewhere, found by
   a. the same fingerprint (company, title, city), or
   b. a similar title (rapidfuzz ratio >= normalize.fuzzy_title_threshold, after expanding
      abbreviations such as "Sr." -> "senior") at the same company, city and seniority:
   - the stored one is a stub and the new one is complete -> NEW, and the stub is marked
     filtered_out "duplicate:<new job_id>" (SUPERSEDES_STUB);
   - otherwise -> DUPLICATE: stored as filtered_out "duplicate:<original job_id>".
3. Otherwise -> NEW (stored with whatever status normalization gave it).
Only active jobs (new, needs_details, matched, no_match) count as originals.
"""

import re
from collections.abc import Mapping, Sequence
from enum import StrEnum

from pydantic import BaseModel
from rapidfuzz import fuzz

from jobmatch.normalize.phrases import fold
from jobmatch.schemas import FilterReason, Job, JobStatus

_ACTIVE = {JobStatus.NEW, JobStatus.NEEDS_DETAILS, JobStatus.MATCHED, JobStatus.NO_MATCH}
_TOKEN = re.compile(r"[^\W_]+|&")


class DedupeDecision(StrEnum):
    NEW = "new"
    FILLS_STUB = "fills_stub"
    SUPERSEDES_STUB = "supersedes_stub"
    ALREADY_STORED = "already_stored"
    DUPLICATE = "duplicate"


class DedupeOutcome(BaseModel):
    decision: DedupeDecision
    job_id: str
    other_job_id: str | None = None  # the original, or the stub that was replaced
    similar_title: bool = False  # found by the fuzzy title rule, not the exact fingerprint


class DedupeResult(BaseModel):
    to_add: list[Job]  # new rows, in input order
    to_replace: list[Job]  # changed versions of stored rows
    outcomes: list[DedupeOutcome]  # one per incoming job, in input order


class TitleSimilarity:
    """Same company + city + seniority, and titles at least `threshold` similar (0-100)."""

    def __init__(self, threshold: float, abbreviations: Mapping[str, str] | None = None) -> None:
        self.threshold = threshold
        self._abbreviations = {fold(k): fold(v) for k, v in (abbreviations or {}).items()}

    def title_key(self, title: str | None) -> str:
        tokens = _TOKEN.findall(fold(title or ""))
        return " ".join(self._abbreviations.get(t, t) for t in tokens)

    @staticmethod
    def group(job: Job) -> tuple[str, str, str] | None:
        """Jobs are only compared inside one group; no company means no fuzzy matching."""
        if not job.company_norm or not job.title_norm:
            return None
        return (fold(job.company_norm), fold(job.city or ""), job.seniority or "")

    def similar(self, a: Job, b: Job) -> bool:
        score = fuzz.ratio(self.title_key(a.title_norm), self.title_key(b.title_norm))
        return score >= self.threshold


def duplicate_reason(original_job_id: str) -> str:
    return f"{FilterReason.DUPLICATE.value}:{original_job_id}"


def dedupe(
    existing: Sequence[Job],
    incoming: Sequence[Job],
    *,
    similarity: TitleSimilarity | None = None,
) -> DedupeResult:
    current: dict[str, Job] = {job.job_id: job for job in existing}
    by_key = {(job.source, job.source_id): job.job_id for job in existing}
    by_fingerprint: dict[str, str] = {}
    by_group: dict[tuple[str, str, str], list[str]] = {}
    stored_ids = set(current)
    added: list[str] = []
    replaced: list[str] = []
    outcomes: list[DedupeOutcome] = []

    def index(job: Job, job_id: str) -> None:
        if job.status not in _ACTIVE:
            return
        if job.fingerprint:
            by_fingerprint[job.fingerprint] = job_id
        group = TitleSimilarity.group(job) if similarity else None
        if group is not None and job_id not in by_group.setdefault(group, []):
            by_group[group].append(job_id)

    def put(job: Job) -> None:
        if job.job_id not in current:
            added.append(job.job_id)
        elif job.job_id in stored_ids and job.job_id not in replaced:
            replaced.append(job.job_id)
        current[job.job_id] = job

    def similar_original(job: Job) -> str | None:
        group = TitleSimilarity.group(job) if similarity else None
        if similarity is None or group is None:
            return None
        for candidate_id in by_group.get(group, []):
            candidate = current[candidate_id]
            if candidate.status in _ACTIVE and similarity.similar(job, candidate):
                return candidate_id
        return None

    for job in existing:
        index(job, job.job_id)

    for job in incoming:
        known_id = by_key.get((job.source, job.source_id))
        if known_id is not None:
            known = current[known_id]
            if known.status is JobStatus.NEEDS_DETAILS and job.description_clean:
                filled = job.model_copy(
                    update={"job_id": known_id, "first_seen_at": known.first_seen_at}
                )
                put(filled)
                index(filled, known_id)
                outcomes.append(DedupeOutcome(decision=DedupeDecision.FILLS_STUB, job_id=known_id))
            else:
                outcomes.append(
                    DedupeOutcome(decision=DedupeDecision.ALREADY_STORED, job_id=known_id)
                )
            continue

        by_key[(job.source, job.source_id)] = job.job_id
        original_id: str | None = None
        by_title = False
        if job.status in _ACTIVE:
            original_id = by_fingerprint.get(job.fingerprint) if job.fingerprint else None
            if original_id is not None and current[original_id].status not in _ACTIVE:
                original_id = None  # e.g. a stub already replaced in this batch
            if original_id is None:
                original_id = similar_original(job)
                by_title = original_id is not None

        if original_id is not None:
            original = current[original_id]
            if original.status is JobStatus.NEEDS_DETAILS and job.status is JobStatus.NEW:
                put(job)
                put(
                    original.model_copy(
                        update={
                            "status": JobStatus.FILTERED_OUT,
                            "filter_reason": duplicate_reason(job.job_id),
                        }
                    )
                )
                index(job, job.job_id)
                outcomes.append(
                    DedupeOutcome(
                        decision=DedupeDecision.SUPERSEDES_STUB,
                        job_id=job.job_id,
                        other_job_id=original_id,
                        similar_title=by_title,
                    )
                )
            else:
                put(
                    job.model_copy(
                        update={
                            "status": JobStatus.FILTERED_OUT,
                            "filter_reason": duplicate_reason(original_id),
                        }
                    )
                )
                outcomes.append(
                    DedupeOutcome(
                        decision=DedupeDecision.DUPLICATE,
                        job_id=job.job_id,
                        other_job_id=original_id,
                        similar_title=by_title,
                    )
                )
            continue

        put(job)
        index(job, job.job_id)
        outcomes.append(DedupeOutcome(decision=DedupeDecision.NEW, job_id=job.job_id))

    return DedupeResult(
        to_add=[current[i] for i in added],
        to_replace=[current[i] for i in replaced],
        outcomes=outcomes,
    )
