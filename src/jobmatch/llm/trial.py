"""Phase 4: judge one job with one real call and keep everything for inspection.

The answer is parsed against JudgeOutput, its structure checked (prompt.check_output) and
its evidence quotes verified (evidence.verify_evidence). The result file maps aliases back
to talent IDs, so it stays on this machine (data/llm_results/, git-ignored).
"""

import json
from pathlib import Path

from pydantic import BaseModel, ValidationError

from jobmatch.llm.anonymize import Roster
from jobmatch.llm.evidence import verify_evidence
from jobmatch.llm.prompt import PROMPT_VERSION, JudgeOutput, check_output
from jobmatch.schemas import LlmUsage
from jobmatch.storage.files import atomic_write_text


class Judged(BaseModel):
    output: JudgeOutput | None  # with unverified quotes removed
    problems: list[str]
    unverified: dict[str, list[str]]
    error: str | None = None


def judge_answer(text: str, job_text: str, roster: Roster, top_k: int) -> Judged:
    try:
        raw = JudgeOutput.model_validate(json.loads(text))
    except (ValueError, ValidationError) as exc:
        return Judged(
            output=None, problems=[], unverified={}, error=f"not the expected JSON: {exc}"
        )
    problems = check_output(raw, list(roster.aliases), top_k)
    report = verify_evidence(raw, job_text, roster)
    return Judged(output=report.output, problems=problems, unverified=report.unverified)


class TrialResult(BaseModel):
    job_id: str
    model: str
    prompt_version: str = PROMPT_VERSION
    judge_key: str
    usage: LlmUsage
    aliases: dict[str, str]  # alias -> talent_id (local only)
    judged: Judged | None
    raw_text: str | None  # the answer as received, for checking the parser


def write_result(result: TrialResult, folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    atomic_write_text(path, result.model_dump_json(indent=2) + "\n")
    return path
