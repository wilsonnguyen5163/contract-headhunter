"""Turn a cached Gemini judgment (TrialResult) into data/llm_matches.csv rows and a job status
(phase 6). Pure functions: no API call, no file access.

Which contractors are kept (user rule, 2026-11-06; numbers in llm.shortlist):
- a job with at least one "fit": fits only, the best `max_fits` (partials are dropped);
- a job with no fit but some "partial": the best `partials_when_no_fit` partials, for a
  person to check;
- a job with neither: no row, status no_match.

Order ("best"), v8 (user rule 2026-11-08): the model screens everyone, reviews up to top_k
(5) in depth, and the app shows the best of those: the deep reviews ranked by their final
verdict (fit before partial), then fit_score; then contractors screened fit or partial but
not reviewed (detailed=False, reason only), in verdict order. A deep review's own verdict and
justification replace the one-line screening. Gemini
sometimes rates someone fit or partial while saying "no_fit", or leaves them out of "top";
they still count, so a self-contradicting answer never hides a candidate.

The score decides (user rule 2026-11-08): for a contractor reviewed in depth, the verdict
shown comes from the fit_score (prompt.SCORE_RANGES): 70+ fit, 40-69 partial, below 40 not
shown. Both directions, so a partial is never green and a fit never yellow.

The "Meets" details are a table (v10): their skill or result, copied from the profile, against
a short summary of the job requirement it satisfies. Both copied texts were checked word for
word in evidence.py before the row got here. Stored in llm_matches.csv's requirements_met
column as "skill → requirement" (MEETS_SEP), so the CSV layout is unchanged; the screen
splits it into two columns.

Text a person reads is in plain English: if a model writes our own words for things ("the
card", "alias"), plain_words turns them into everyday ones ("their profile").

Aliases are mapped back to talent IDs here, on this machine (the row's talent_id). The text
shown to a person names nobody by alias or ID (user rule 2026-11-08: the screen already shows
the contractor's name, profile and resume). The prompt forbids it; if a model still writes an
alias in its own text (reason, requirements, red flags), drop_aliases removes it. A
contractor who lost a "meets" row in the check is marked unverified_evidence. The dropped
verdicts stay in the cached judgment.
"""

import re
from collections.abc import Mapping

from pydantic import BaseModel

from jobmatch.config import LlmShortlistSettings
from jobmatch.llm.prompt import SCORE_RANGES, MeetRow, TopMatch
from jobmatch.llm.trial import TrialResult
from jobmatch.schemas import JobStatus, LlmMatch, LlmVerdict

# Joins a "meets" row's two columns in the stored text (assumption: never in the text
# itself; if it is, it's replaced with "-"). app/ui.py splits on it.
MEETS_SEP = " → "


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


def verdict_from_score(score: int) -> str:
    """The verdict a fit_score stands for (prompt.SCORE_RANGES): fit, partial, or no."""
    for verdict in ("fit", "partial"):
        low, high = SCORE_RANGES[verdict]
        if low <= score <= high:
            return verdict
    return "no"


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
        # v8: for a contractor the model reviewed in depth, its final verdict and
        # justification win over the one-line screening.
        final = top.verdict if top is not None and top.verdict is not None else v.verdict
        reason = top.reason if top is not None and top.reason.strip() else v.reason
        seen.add(alias)  # judged once, whatever the outcome
        if top is not None:
            # The score decides (user rule 2026-11-08): 70+ fit, 40-69 partial, below 40 no,
            # whatever verdict the model wrote, so a chip's colour always matches its label.
            final = verdict_from_score(top.fit_score)
        if final not in (LlmVerdict.FIT, LlmVerdict.PARTIAL):
            return  # judged "no"
        verdict = LlmVerdict(final)
        out.append(
            _Candidate(
                alias=alias,
                talent_id=talent_id,
                verdict=verdict,
                reason=reason or None,
                top=top,
            )
        )

    for top in output.top:
        add(top.alias, top)
    # v8 (user rule): the deep reviews decide who is shown. Rank them by final verdict (fit
    # before partial), then fit_score; Python's sort is stable, so ties keep the model's order.
    out.sort(key=lambda c: (c.verdict is not LlmVerdict.FIT, -(c.top.fit_score if c.top else 0)))
    for v in output.verdicts:
        add(v.alias, None)  # screened fit/partial but not reviewed in depth: after them
    return out


_ALIAS = re.compile(r"\bC\d{2,}\b")  # the aliases anonymize.build_roster gives: C01, C034…


# "C34 is a …", "C34 has …", "C34 est une …" at the start: the alias, verb and article go.
_LEADING = re.compile(r"^\s*(C\d{2,})\s+(?:is|has|est)\s+(?:an?\s+|une?\s+)?", re.I)


def drop_aliases(text: str, aliases: Mapping[str, str]) -> str:
    """Remove this roster's aliases from a model's own text, naming nobody instead:
    "C34 is a senior writer…" -> "Senior writer…" (French "C34 est une…" likewise); any other
    mention -> "this contractor" ("ce contractuel" is not attempted: English wording is used).
    Only known aliases are touched, so other codes in the text are left alone."""
    lead = _LEADING.match(text)
    if lead and lead.group(1) in aliases:
        rest = text[lead.end() :]
        text = rest[:1].upper() + rest[1:]
    out = _ALIAS.sub(lambda m: "this contractor" if m.group(0) in aliases else m.group(0), text)
    return out[:1].upper() + out[1:] if out.startswith("this contractor") else out


# Our own words for things, if a model uses them in text a recruiter reads (user rule
# 2026-11-08: plain English). "the card" / "their card" / "contractor's card" -> "their
# profile"; "alias" -> "contractor".
_CARD = re.compile(r"\b(?:the |their |this |the contractor's |contractor's )?cards?\b", re.I)
_ALIAS_WORD = re.compile(r"\baliases\b|\balias\b", re.I)


def plain_words(text: str) -> str:
    """Replace our technical words with everyday ones, keeping a capital at the start."""

    def card(m: re.Match[str]) -> str:
        return "Their profile" if m.group(0)[0].isupper() else "their profile"

    return _ALIAS_WORD.sub("contractor", _CARD.sub(card, text))


def select(candidates: list[_Candidate], rules: LlmShortlistSettings) -> list[_Candidate]:
    fits = [c for c in candidates if c.verdict is LlmVerdict.FIT]
    if fits:
        return fits[: rules.max_fits]
    partials = [c for c in candidates if c.verdict is LlmVerdict.PARTIAL]
    return partials[: rules.partials_when_no_fit]


def judged_job(
    result: TrialResult, rules: LlmShortlistSettings, *, everyone: bool = False
) -> JudgedJob | None:
    """None when the judgment has no usable answer (the job keeps its status). `everyone`:
    a row for every contractor judged fit or partial, best first, not just the shortlist
    (0.4: the job's candidate pool; llm/rounds.py picks who is shown)."""
    if result.judged is None or result.judged.output is None:
        return None
    unverified = result.judged.unverified
    rows: list[LlmMatch] = []

    def clean(text: str) -> str:
        return plain_words(drop_aliases(text.strip(), result.aliases))

    def named(texts: list[str]) -> list[str]:
        return [clean(t) for t in texts if t.strip()]

    def meets(items: list[MeetRow]) -> list[str]:
        """Rows checked in evidence.py, stored as "skill → requirement summary" (MEETS_SEP).
        Older answers have no requirement: just the skill."""
        out = []
        for row in items:
            if not row.skill.strip():
                continue
            skill = clean(row.skill).replace(MEETS_SEP.strip(), "-")
            summary = clean(row.requirement).replace(MEETS_SEP.strip(), "-")
            out.append(f"{skill}{MEETS_SEP}{summary}" if summary else skill)
        return out

    candidates = _candidates(result)
    for c in candidates if everyone else select(candidates, rules):
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
                reason=clean(c.reason) if c.reason else None,
                requirements_met=meets(top.meets) if top else [],
                requirements_missing=named(top.requirements_missing) if top else [],
                red_flags=(
                    named([f"{f.kind}: {f.detail}" for f in top.red_flags if f.detail.strip()])
                    if top
                    else []
                ),
                evidence=[],  # v10: no separate evidence quotes (the meets rows are checked)
                unverified_evidence=bool(top and unverified.get(c.alias)),
                model=result.model,
                prompt_version=result.prompt_version,
                judge_key=result.judge_key,
                created_at=result.usage.at,
            )
        )
    status = JobStatus.MATCHED if rows else JobStatus.NO_MATCH
    return JudgedJob(job_id=result.job_id, status=status, matches=rows)
