"""Parse and check the two answers for one job, and keep everything for inspection.

v12: the screening answer (ScreenOutput) and the deep-review answer (ReviewOutput) are parsed
separately, then joined into one JudgeOutput: the screening's verdicts and no_fit, the
review's detailed entries. The joined answer gets the structural checks
(prompt.check_output) and the word-for-word check of every "meets" row
(evidence.verify_evidence). The result file maps aliases back to talent IDs, so it stays on
this machine (data/llm_results/, git-ignored).
"""

import json
from pathlib import Path

from pydantic import BaseModel, ValidationError

from jobmatch.llm.anonymize import Roster
from jobmatch.llm.evidence import verify_evidence
from jobmatch.llm.prompt import (
    PROMPT_VERSION,
    JudgeOutput,
    ReviewBatchOutput,
    ReviewOutput,
    ScreenOutput,
    check_output,
)
from jobmatch.schemas import LlmUsage
from jobmatch.storage.files import atomic_write_text


class Judged(BaseModel):
    output: JudgeOutput | None  # with unconfirmed "meets" rows removed
    problems: list[str]
    unverified: dict[str, list[str]]
    error: str | None = None


class Screened(BaseModel):
    output: ScreenOutput | None  # shortlist cleaned: known, fit or partial, unique, capped
    problems: list[str]
    error: str | None = None


def parse_screen(text: str, roster: Roster, top_k: int) -> Screened:
    try:
        raw = ScreenOutput.model_validate(json.loads(text))
    except (ValueError, ValidationError) as exc:
        return Screened(output=None, problems=[], error=f"not the expected JSON: {exc}")
    problems: list[str] = []
    verdict_of = {v.alias: v.verdict for v in raw.verdicts}
    shortlist: list[str] = []
    for alias in raw.shortlist:
        if alias not in roster.aliases:
            problems.append(f"shortlisted unknown alias {alias}")
        elif verdict_of.get(alias) not in ("fit", "partial"):
            problems.append(f"{alias} shortlisted but screened '{verdict_of.get(alias)}'")
        elif alias not in shortlist:
            shortlist.append(alias)
    if len(shortlist) > top_k:
        problems.append(f"{len(shortlist)} shortlisted, asked for at most {top_k}")
        shortlist = shortlist[:top_k]
    return Screened(output=raw.model_copy(update={"shortlist": shortlist}), problems=problems)


def parse_review(
    text: str, labels: dict[str, str]
) -> tuple[dict[str, ReviewOutput], list[str], str | None]:
    """A batch review answer (v15) split per job: (job_id -> its review, problems, error).
    `labels` maps the ids used in the request ("J1") to job IDs. A job missing from the
    answer is simply absent (it is reviewed again next run); an unknown or repeated id is a
    problem and is ignored."""
    try:
        raw = ReviewBatchOutput.model_validate(json.loads(text))
    except (ValueError, ValidationError) as exc:
        return {}, [], f"not the expected JSON: {exc}"
    reviews: dict[str, ReviewOutput] = {}
    problems: list[str] = []
    for entry in raw.jobs:
        job_id = labels.get(entry.job)
        if job_id is None:
            problems.append(f"review for unknown job {entry.job}")
        elif job_id in reviews:
            problems.append(f"more than one review for job {entry.job}")
        else:
            reviews[job_id] = ReviewOutput(top=entry.top)
    for label, job_id in labels.items():
        if job_id not in reviews:
            problems.append(f"no review for job {label}")
    return reviews, problems, None


def join_answers(
    screen: Screened, review: ReviewOutput | None, job_text: str, roster: Roster, top_k: int
) -> Judged:
    """One JudgeOutput from both calls; review None = nothing was shortlisted."""
    assert screen.output is not None
    shortlist = screen.output.shortlist
    top = [m for m in review.top if m.alias in shortlist] if review else []
    problems = list(screen.problems)
    if review:
        reviewed = {m.alias for m in review.top}
        if extra := sorted(reviewed - set(shortlist)):
            problems.append(f"reviewed but not shortlisted: {', '.join(extra)}")
        if missing := [a for a in shortlist if a not in reviewed]:
            problems.append(f"shortlisted but not reviewed: {', '.join(missing)}")
    joined = JudgeOutput(verdicts=screen.output.verdicts, top=top, no_fit=screen.output.no_fit)
    problems += check_output(joined, list(roster.aliases), top_k)
    report = verify_evidence(joined, job_text, roster)
    return Judged(output=report.output, problems=problems, unverified=report.unverified)


class TrialResult(BaseModel):
    job_id: str
    model: str  # the model whose judgement is shown: the reviewer, or the screener if none
    prompt_version: str = PROMPT_VERSION
    judge_key: str
    usage: LlmUsage  # the screening call (the only call up to v11)
    aliases: dict[str, str]  # alias -> talent_id (local only)
    judged: Judged | None
    raw_text: str | None  # the screening answer as received
    # v12: the deep review (None when nothing was shortlisted, or before v12).
    review_model: str | None = None
    review_usage: LlmUsage | None = None  # the whole review call (shared by its batch)
    review_raw_text: str | None = None
    review_batch: list[str] = []  # v15: the job IDs reviewed in the same call


def write_result(result: TrialResult, folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    atomic_write_text(path, result.model_dump_json(indent=2) + "\n")
    return path
