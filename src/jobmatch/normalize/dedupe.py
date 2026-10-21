"""Dedupe normalized jobs against what is already stored (and within the batch).

Rules, in order, for each incoming job:
1. Same (source, source_id) already stored:
   - the stored one is a stub (needs_details) and the new one has text -> FILLS_STUB
     (the stored row is replaced; its first_seen_at is kept);
   - otherwise -> ALREADY_STORED (nothing written; re-running an import is safe).
2. Same fingerprint (company, title, city) as an active stored job from another posting:
   - the stored one is a stub and the new one is complete -> NEW, and the stub is marked
     filtered_out "duplicate:<new job_id>" (SUPERSEDES_STUB);
   - otherwise -> DUPLICATE: stored as filtered_out "duplicate:<original job_id>".
3. Otherwise -> NEW (stored with whatever status normalization gave it).
Fuzzy title matching (rapidfuzz) is a later addition.
"""

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel

from jobmatch.schemas import FilterReason, Job, JobStatus

_ACTIVE = {JobStatus.NEW, JobStatus.NEEDS_DETAILS, JobStatus.MATCHED, JobStatus.NO_MATCH}


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


class DedupeResult(BaseModel):
    to_add: list[Job]  # new rows, in input order
    to_replace: list[Job]  # changed versions of stored rows
    outcomes: list[DedupeOutcome]  # one per incoming job, in input order


def duplicate_reason(original_job_id: str) -> str:
    return f"{FilterReason.DUPLICATE.value}:{original_job_id}"


def dedupe(existing: Sequence[Job], incoming: Sequence[Job]) -> DedupeResult:
    current: dict[str, Job] = {job.job_id: job for job in existing}
    by_key = {(job.source, job.source_id): job.job_id for job in existing}
    by_fingerprint = {
        job.fingerprint: job.job_id for job in existing if job.fingerprint and job.status in _ACTIVE
    }
    stored_ids = set(current)
    added: list[str] = []
    replaced: list[str] = []
    outcomes: list[DedupeOutcome] = []

    def put(job: Job) -> None:
        if job.job_id not in current:
            added.append(job.job_id)
        elif job.job_id in stored_ids and job.job_id not in replaced:
            replaced.append(job.job_id)
        current[job.job_id] = job

    for job in incoming:
        known_id = by_key.get((job.source, job.source_id))
        if known_id is not None:
            known = current[known_id]
            if known.status is JobStatus.NEEDS_DETAILS and job.description_clean:
                put(
                    job.model_copy(
                        update={"job_id": known_id, "first_seen_at": known.first_seen_at}
                    )
                )
                if job.fingerprint and job.status in _ACTIVE:
                    by_fingerprint[job.fingerprint] = known_id
                outcomes.append(DedupeOutcome(decision=DedupeDecision.FILLS_STUB, job_id=known_id))
            else:
                outcomes.append(
                    DedupeOutcome(decision=DedupeDecision.ALREADY_STORED, job_id=known_id)
                )
            continue

        by_key[(job.source, job.source_id)] = job.job_id
        original_id = by_fingerprint.get(job.fingerprint) if job.fingerprint else None
        if original_id is not None and job.status in _ACTIVE:
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
                by_fingerprint[job.fingerprint] = job.job_id
                outcomes.append(
                    DedupeOutcome(
                        decision=DedupeDecision.SUPERSEDES_STUB,
                        job_id=job.job_id,
                        other_job_id=original_id,
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
                    )
                )
            continue

        put(job)
        if job.fingerprint and job.status in _ACTIVE:
            by_fingerprint[job.fingerprint] = job.job_id
        outcomes.append(DedupeOutcome(decision=DedupeDecision.NEW, job_id=job.job_id))

    return DedupeResult(
        to_add=[current[i] for i in added],
        to_replace=[current[i] for i in replaced],
        outcomes=outcomes,
    )
