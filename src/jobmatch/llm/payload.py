"""The job text sent to the API, pruned with what normalization already extracted
(doc/V1_PLAN.md section 6).

Sent: title, seniority, years of experience, one contract line, responsibilities,
requirements and nice-to-have (duplicates dropped), or the clean description when the
posting has no lists. Not sent: the raw description, employer pitch, benefits, company,
URL and pay. Past llm.payload.max_job_tokens, items are dropped from the end: nice-to-have
first, then the longer of responsibilities and requirements.

Posting text is untrusted: angle brackets are replaced so it cannot close the <job> block
that wraps it in the prompt.
"""

import hashlib
import math
import re

from pydantic import BaseModel

from jobmatch.config import LlmPayloadSettings
from jobmatch.normalize.contact import EMAIL
from jobmatch.schemas import (
    Commitment,
    EmploymentType,
    Job,
    JobAvailability,
    JobStatus,
    RemoteType,
)

# Jobs a run judges: the same set v0 matched (kept here so v1 does not import match/).
ACTIVE_STATUSES = (JobStatus.NEW, JobStatus.MATCHED, JobStatus.NO_MATCH)


def is_active(job: Job) -> bool:
    """Sent to Gemini and shown in Results: an imported contract job that the reviewer has
    not marked completed or unavailable."""
    return job.status in ACTIVE_STATUSES and job.availability is JobAvailability.AVAILABLE


_SPACES = re.compile(r"\s+")


def estimate_tokens(text: str, chars_per_token: float) -> int:
    """Local estimate before a call; the API's own count is logged after it."""
    return math.ceil(len(text) / chars_per_token) if text else 0


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def neutralize(text: str) -> str:
    """One line of untrusted text: whitespace collapsed, angle brackets replaced, email
    addresses masked (a posting's HR contact is personal data and says nothing about fit)."""
    text = EMAIL.sub("[email]", text)
    return _SPACES.sub(" ", text).replace("<", "‹").replace(">", "›").strip()


def _unique(items: list[str], seen: set[str]) -> list[str]:
    kept: list[str] = []
    for item in items:
        line = neutralize(item)
        key = line.casefold()
        if line and key not in seen:
            seen.add(key)
            kept.append(line)
    return kept


class JobCard(BaseModel):
    job_id: str
    title: str
    seniority: str | None = None
    years_experience_min: float | None = None
    contract: str | None = None
    language: str | None = None
    responsibilities: list[str] = []
    requirements: list[str] = []
    nice_to_have: list[str] = []
    description: str | None = None  # only when the posting has no lists
    hours: str | None = None  # as a reviewer entered it (jobs CSV / form)


def _contract_line(job: Job) -> str | None:
    parts: list[str] = []
    if job.employment_type is not EmploymentType.UNKNOWN:
        parts.append(job.employment_type.value)
    if job.duration_months:
        parts.append(f"{job.duration_months:g} months")
    if job.commitment is not Commitment.UNKNOWN:
        parts.append(job.commitment.value.replace("_", "-"))
    if job.remote_type is not RemoteType.UNKNOWN:
        parts.append(job.remote_type.value)
    if job.province:
        parts.append(job.province.removeprefix("CA-"))
    return "; ".join(parts) or None


def render_job(card: JobCard) -> str:
    lines = [f"Title: {card.title}"]
    if card.seniority:
        lines.append(f"Seniority: {card.seniority}")
    if card.years_experience_min is not None:
        lines.append(f"Years of experience required: {card.years_experience_min:g}")
    if card.contract:
        lines.append(f"Contract: {card.contract}")
    if card.language:
        lines.append(f"Posting language: {card.language}")
    if card.hours:
        lines.append(f"Hours: {card.hours}")
    for label, items in (
        ("Responsibilities", card.responsibilities),
        ("Requirements", card.requirements),
        ("Nice to have", card.nice_to_have),
    ):
        if items:
            lines.append(f"{label}:")
            lines += [f"- {item}" for item in items]
    if card.description:
        lines.append(f"Description: {card.description}")
    return "\n".join(lines)


def job_card(job: Job, settings: LlmPayloadSettings) -> JobCard:
    seen: set[str] = set()
    responsibilities = _unique(job.responsibilities, seen)
    requirements = _unique(job.requirements, seen)
    nice_to_have = _unique(job.nice_to_have, seen)
    description = None
    if not (responsibilities or requirements or nice_to_have) and job.description_clean:
        description = neutralize(job.description_clean)
    card = JobCard(
        job_id=job.job_id,
        title=neutralize(job.title_norm or job.title_raw),
        seniority=job.seniority,
        years_experience_min=job.years_experience_min,
        contract=_contract_line(job),
        language=job.language.value if job.language else None,
        responsibilities=responsibilities,
        requirements=requirements,
        nice_to_have=nice_to_have,
        description=description,
        hours=f"{job.hours_per_week:g} per week" if job.hours_per_week else None,
    )
    return _fit(card, settings)


def _fit(card: JobCard, settings: LlmPayloadSettings) -> JobCard:
    """Drop items from the end until the rendered text fits max_job_tokens."""

    def size(c: JobCard) -> int:
        return estimate_tokens(render_job(c), settings.chars_per_token)

    while size(card) > settings.max_job_tokens:
        if card.nice_to_have:
            card.nice_to_have = card.nice_to_have[:-1]
        elif len(card.responsibilities) > 1 or len(card.requirements) > 1:
            longer = (
                "responsibilities"
                if len(card.responsibilities) >= len(card.requirements)
                else "requirements"
            )
            setattr(card, longer, getattr(card, longer)[:-1])
        elif card.description:
            over = size(card) - settings.max_job_tokens
            keep = max(len(card.description) - math.ceil(over * settings.chars_per_token), 0)
            card.description = card.description[:keep].rsplit(" ", 1)[0] + " …"
            if keep == 0:
                card.description = None
        else:
            break  # title and one item each: leave as is
    return card
