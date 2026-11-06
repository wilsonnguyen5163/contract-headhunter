"""llm-preview: build the exact requests a run would send, without sending anything.

- write_preview: one JSON file per job with the request body, plus aliases.json (the
  alias-to-talent-ID map, local only), so a person can read what would leave the machine.
- preview_stats: token estimates over every active job, and what a full run would cost in
  requests and days at our limits.
"""

import json
import re
import statistics
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from jobmatch.config import LlmSettings
from jobmatch.llm.anonymize import Roster
from jobmatch.llm.gemini import build_body
from jobmatch.llm.payload import estimate_tokens, job_card, render_job
from jobmatch.llm.prompt import PROMPT_VERSION, PreparedCall, prepare_call
from jobmatch.schemas import ContractorProfile, Job
from jobmatch.storage.files import atomic_write_text

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(job_id: str) -> str:
    return _UNSAFE.sub("_", job_id)


def preview_calls(jobs: Iterable[Job], roster: Roster, settings: LlmSettings) -> list[PreparedCall]:
    return [prepare_call(job_card(j, settings.payload), roster, settings) for j in jobs]


def write_preview(calls: Sequence[PreparedCall], settings: LlmSettings, folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for call in calls:
        path = folder / f"{safe_name(call.job_ids[0])}.json"
        payload = {
            "job_ids": call.job_ids,
            "model": settings.model,
            "prompt_version": PROMPT_VERSION,
            "judge_key": call.judge_key(settings.model),
            "estimated_input_tokens": call.estimated_input_tokens,
            "estimated_total_tokens": call.estimated_total_tokens,
            "body": build_body(settings, call.request),
        }
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        written.append(path)
    aliases = calls[0].aliases if calls else {}
    atomic_write_text(folder / "aliases.json", json.dumps(aliases, indent=2) + "\n")
    written.append(folder / "aliases.json")
    return written


def preview_folder(data_dir: Path, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return data_dir / "llm_preview" / stamp


class Spread(BaseModel):
    median: int
    p90: int
    max: int

    @classmethod
    def of(cls, values: Sequence[int]) -> "Spread":
        if not values:
            return cls(median=0, p90=0, max=0)
        ordered = sorted(values)
        p90 = ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))]
        return cls(median=int(statistics.median(ordered)), p90=p90, max=ordered[-1])


class PreviewStats(BaseModel):
    jobs: int
    contractors: int
    excluded: int
    roster_tokens: int
    job_tokens: Spread
    call_input_tokens: Spread
    output_tokens_per_call: int
    calls: int
    total_tokens: int
    days_at_rpd: float
    largest_call_job: str | None


def preview_stats(jobs: Sequence[Job], roster: Roster, settings: LlmSettings) -> PreviewStats:
    """Setup B of doc/V1_PLAN.md section 5: every job against the whole roster, one per call."""
    cpt = settings.payload.chars_per_token
    calls = preview_calls(jobs, roster, settings)
    job_tokens = [estimate_tokens(render_job(job_card(j, settings.payload)), cpt) for j in jobs]
    inputs = [c.estimated_input_tokens for c in calls]
    n_calls = -(-len(calls) // settings.jobs_per_call)  # jobs_per_call > 1: rough only
    largest = max(calls, key=lambda c: c.estimated_input_tokens) if calls else None
    return PreviewStats(
        jobs=len(jobs),
        contractors=len(roster.cards),
        excluded=len(roster.excluded),
        roster_tokens=estimate_tokens(roster.text(), cpt),
        job_tokens=Spread.of(job_tokens),
        call_input_tokens=Spread.of(inputs),
        output_tokens_per_call=settings.payload.expected_output_tokens,
        calls=n_calls,
        total_tokens=sum(c.estimated_total_tokens for c in calls),
        days_at_rpd=round(n_calls / settings.limits.rpd, 2),
        largest_call_job=largest.job_ids[0] if largest else None,
    )


def profession_problems(
    profiles: Iterable[ContractorProfile], known: Iterable[str]
) -> tuple[int, list[str]]:
    """(contractors without a primary profession, talent IDs whose professions are not in
    professions.yaml)."""
    names = set(known)
    missing = 0
    unknown: list[str] = []
    for p in profiles:
        if not p.primary_profession:
            missing += 1
        used = [x for x in (p.primary_profession, *p.secondary_professions) if x]
        if any(x not in names for x in used):
            unknown.append(p.talent_id)
    return missing, unknown
