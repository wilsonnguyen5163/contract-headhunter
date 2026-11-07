"""Turn a cached Gemini judgment (TrialResult) into data/llm_matches.csv rows and a job status
(phase 6). Pure functions: no API call, no file access.

Which contractors are kept (user rule, 2026-11-06; numbers in llm.shortlist):
- a job with at least one "fit": fits only, the best `max_fits` (partials are dropped);
- a job with no fit but some "partial": the best `partials_when_no_fit` partials, for a
  person to check;
- a job with neither: no row, status no_match.

Order ("best"): Gemini's detailed list ("top") first, in its order, then contractors it
judged but did not describe (detailed=False, reason only), in verdict order. Gemini
sometimes rates someone fit or partial while saying "no_fit", or leaves them out of "top";
they still count, so a self-contradicting answer never hides a candidate.

Aliases are mapped back to talent IDs here, on this machine. Evidence quotes were already
checked against the text sent (evidence.py); a contractor who lost a quote is marked
unverified_evidence. The dropped verdicts stay in the cached judgment.
"""

from pydantic import BaseModel

from jobmatch.config import LlmShortlistSettings
from jobmatch.llm.prompt import TopMatch
from jobmatch.llm.trial import TrialResult
from jobmatch.schemas import JobStatus, LlmMatch, LlmVerdict


class JudgedJob(BaseModel):
    job_id: str
    status: JobStatus  # matched (someone kept) or no_match
    matches: list[LlmMatch]


class _Candidate(BaseModel):
    alias: str
    talent_id: str
    verdict: LlmVerdict
    reason: str | None
    top: TopMatch | None  # None: not described by Gemini


def _candidates(result: TrialResult) -> list[_Candidate]:
    """Every contractor judged fit or partial, best first (see module docstring)."""
    assert result.judged is not None and result.judged.output is not None
    output = result.judged.output
    verdicts = {v.alias: v for v in output.verdicts}
    seen: set[str] = set()
    out: list[_Candidate] = []

    def add(alias: str, top: TopMatch | None) -> None:
        v = verdicts.get(alias)
        talent_id = result.aliases.get(alias)
        if alias in seen or v is None or talent_id is None:
            return  # repeated, or unknown alias: already reported as a problem
        if v.verdict not in (LlmVerdict.FIT, LlmVerdict.PARTIAL):
            return  # judged "no"
        seen.add(alias)
        out.append(
            _Candidate(
                alias=alias,
                talent_id=talent_id,
                verdict=LlmVerdict(v.verdict),
                reason=v.reason or None,
                top=top,
            )
        )

    for top in output.top:
        add(top.alias, top)
    for v in output.verdicts:
        add(v.alias, None)
    return out


def select(candidates: list[_Candidate], rules: LlmShortlistSettings) -> list[_Candidate]:
    fits = [c for c in candidates if c.verdict is LlmVerdict.FIT]
    if fits:
        return fits[: rules.max_fits]
    partials = [c for c in candidates if c.verdict is LlmVerdict.PARTIAL]
    return partials[: rules.partials_when_no_fit]


def judged_job(result: TrialResult, rules: LlmShortlistSettings) -> JudgedJob | None:
    """None when the judgment has no usable answer (the job keeps its status)."""
    if result.judged is None or result.judged.output is None:
        return None
    unverified = result.judged.unverified
    rows: list[LlmMatch] = []
    for c in select(_candidates(result), rules):
        top = c.top
        rows.append(
            LlmMatch(
                match_id=f"{result.judge_key}-{c.talent_id}",
                job_id=result.job_id,
                talent_id=c.talent_id,
                rank=len(rows) + 1,
                verdict=c.verdict,
                detailed=top is not None,
                fit_score=top.fit_score if top else None,
                reason=c.reason,
                requirements_met=[r for r in top.requirements_met if r.strip()] if top else [],
                requirements_missing=(
                    [r for r in top.requirements_missing if r.strip()] if top else []
                ),
                red_flags=(
                    [f"{f.kind}: {f.detail}" for f in top.red_flags if f.detail.strip()]
                    if top
                    else []
                ),
                evidence=(
                    [f"{e.source}: {e.quote}" for e in top.evidence if e.quote.strip()]
                    if top
                    else []
                ),
                unverified_evidence=bool(top and unverified.get(c.alias)),
                model=result.model,
                prompt_version=result.prompt_version,
                judge_key=result.judge_key,
                created_at=result.usage.at,
            )
        )
    status = JobStatus.MATCHED if rows else JobStatus.NO_MATCH
    return JudgedJob(job_id=result.job_id, status=status, matches=rows)
