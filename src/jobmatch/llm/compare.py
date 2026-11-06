"""Phase 5: compare Gemini's verdicts with the blind labels and with v0's latest stored run.

Nothing is re-scored with v0 (v1 rule): v0 is read from data/matches.csv and
matches_flagged.csv, keeping only the newest scoring version. Those files hold v0's
shortlist (top 5 above min_score) only, so a labelled pair v0 did not shortlist has no v0
score. v0's full-matrix numbers stay as recorded in doc/PROTOTYPES.md.

Measures, on labelled pairs of the jobs Gemini judged:
- agreement: Gemini verdict vs label (confusion table, exact agreement, Cohen's kappa);
- surfaced: what each system puts in front of the reviewer (Gemini: fit or partial; v0: the
  shortlist), split by label;
- within-job ranking: chance that a labelled fit ranks above a labelled "no" in the same job
  (ties count half), with a bootstrap over jobs for the Gemini minus v0 difference;
- jobs: "nothing to show" (Gemini no_fit, v0 empty shortlist) vs whether a labelled fit exists.
"""

import csv
import random
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from jobmatch.llm.trial import TrialResult
from jobmatch.schemas import Match

Label = Literal["fit", "partial", "no"]
LABELS: tuple[Label, ...] = ("fit", "partial", "no")
VERDICT_RANK = {"no": 0, "partial": 1, "fit": 2}


# ---- inputs ----


class LabelledPair(BaseModel):
    job_id: str
    contractor_id: str
    label: Label
    source: str = ""


def read_labels(path: Path) -> list[LabelledPair]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [
            LabelledPair(
                job_id=row["job_id"],
                contractor_id=row["contractor_id"],
                label=row["label"].strip().casefold(),  # type: ignore[arg-type]
                source=row.get("source") or "",
            )
            for row in csv.DictReader(f)
        ]


class V0Pair(BaseModel):
    score: float
    band: str
    rank: int
    flagged: bool


class V0Run(BaseModel):
    version: str
    created_at: datetime | None
    pairs: dict[tuple[str, str], V0Pair]  # (job_id, contractor_id)

    def shortlist(self, job_id: str) -> list[str]:
        return [c for (j, c), p in self.pairs.items() if j == job_id and not p.flagged]


def latest_v0(matches: Iterable[Match], flagged: Iterable[Match]) -> V0Run:
    rows = [(m, False) for m in matches] + [(m, True) for m in flagged]
    if not rows:
        return V0Run(version="", created_at=None, pairs={})
    newest = max(rows, key=lambda r: r[0].created_at)[0]
    version = newest.scoring_version
    pairs = {
        (m.job_id, m.contractor_id): V0Pair(
            score=m.score, band=m.band.value, rank=m.rank, flagged=f
        )
        for m, f in rows
        if m.scoring_version == version
    }
    return V0Run(version=version, created_at=newest.created_at, pairs=pairs)


class GeminiPair(BaseModel):
    verdict: Label
    reason: str
    in_top: bool
    fit_score: int | None


class GeminiJob(BaseModel):
    job_id: str
    no_fit: bool
    pairs: dict[str, GeminiPair]  # contractor_id ->
    problems: list[str]


def gemini_jobs(results: Iterable[TrialResult]) -> dict[str, GeminiJob]:
    jobs: dict[str, GeminiJob] = {}
    for r in results:
        if r.judged is None or r.judged.output is None:
            continue
        out = r.judged.output
        top = {m.alias: m.fit_score for m in out.top}
        pairs = {
            r.aliases[v.alias]: GeminiPair(
                verdict=v.verdict,
                reason=v.reason,
                in_top=v.alias in top,
                fit_score=top.get(v.alias),
            )
            for v in out.verdicts
            if v.alias in r.aliases
        }
        jobs[r.job_id] = GeminiJob(
            job_id=r.job_id, no_fit=out.no_fit, pairs=pairs, problems=r.judged.problems
        )
    return jobs


# ---- per pair ----


class PairRow(BaseModel):
    job_id: str
    contractor_id: str
    label: Label
    source: str
    gemini_verdict: Label | None
    gemini_in_top: bool
    gemini_fit_score: int | None
    gemini_reason: str | None
    v0_shortlisted: bool
    v0_flagged: bool
    v0_score: float | None
    v0_rank: int | None
    v0_band: str | None

    @property
    def gemini_surfaced(self) -> bool:
        return self.gemini_verdict in ("fit", "partial")


def pair_rows(
    labels: Iterable[LabelledPair], gemini: dict[str, GeminiJob], v0: V0Run
) -> list[PairRow]:
    rows: list[PairRow] = []
    for lp in labels:
        job = gemini.get(lp.job_id)
        if job is None:
            continue  # job not judged by Gemini (excluded, or not run yet)
        g = job.pairs.get(lp.contractor_id)
        v = v0.pairs.get((lp.job_id, lp.contractor_id))
        rows.append(
            PairRow(
                job_id=lp.job_id,
                contractor_id=lp.contractor_id,
                label=lp.label,
                source=lp.source,
                gemini_verdict=g.verdict if g else None,
                gemini_in_top=g.in_top if g else False,
                gemini_fit_score=g.fit_score if g else None,
                gemini_reason=g.reason if g else None,
                v0_shortlisted=v is not None and not v.flagged,
                v0_flagged=v is not None and v.flagged,
                v0_score=v.score if v else None,
                v0_rank=v.rank if v else None,
                v0_band=v.band if v else None,
            )
        )
    return rows


# ---- measures ----


def confusion(rows: Iterable[PairRow]) -> dict[str, dict[str, int]]:
    table = {label: {v: 0 for v in (*LABELS, "missing")} for label in LABELS}
    for r in rows:
        table[r.label][r.gemini_verdict or "missing"] += 1
    return table


def kappa(rows: Sequence[PairRow]) -> float | None:
    judged = [r for r in rows if r.gemini_verdict is not None]
    n = len(judged)
    if not n:
        return None
    observed = sum(r.label == r.gemini_verdict for r in judged) / n
    expected = sum(
        (sum(r.label == c for r in judged) / n) * (sum(r.gemini_verdict == c for r in judged) / n)
        for c in LABELS
    )
    return None if expected == 1 else round((observed - expected) / (1 - expected), 3)


class Surfaced(BaseModel):
    """What a system put in front of the reviewer, among labelled pairs, by label."""

    system: str
    by_label: dict[str, tuple[int, int]]  # label -> (surfaced, total)
    surfaced: int
    fit_share: float | None  # labelled fits among surfaced labelled pairs
    no_share: float | None  # labelled "no" among surfaced labelled pairs


def surfaced(rows: Sequence[PairRow], system: str, test: Callable[[PairRow], bool]) -> Surfaced:
    by_label = {
        label: (sum(test(r) for r in rows if r.label == label), sum(r.label == label for r in rows))
        for label in LABELS
    }
    total = sum(s for s, _ in by_label.values())
    return Surfaced(
        system=system,
        by_label=by_label,
        surfaced=total,
        fit_share=round(by_label["fit"][0] / total, 3) if total else None,
        no_share=round(by_label["no"][0] / total, 3) if total else None,
    )


def gemini_order(r: PairRow) -> float:
    if r.gemini_verdict is None:
        return -1.0
    return VERDICT_RANK[r.gemini_verdict] * 1000 + (r.gemini_fit_score or 0)


def v0_order(r: PairRow) -> float:
    """v0 shortlist score; pairs v0 did not keep (or flagged) share the bottom place."""
    return r.v0_score if r.v0_shortlisted and r.v0_score is not None else -1.0


def _wins(rows: Sequence[PairRow], order: Callable[[PairRow], float]) -> tuple[float, int]:
    wins, pairs = 0.0, 0
    for fit in (r for r in rows if r.label == "fit"):
        for no in (r for r in rows if r.label == "no"):
            a, b = order(fit), order(no)
            wins += 1.0 if a > b else 0.5 if a == b else 0.0
            pairs += 1
    return wins, pairs


def within_job(rows: Sequence[PairRow], order: Callable[[PairRow], float]) -> float | None:
    by_job: dict[str, list[PairRow]] = defaultdict(list)
    for r in rows:
        by_job[r.job_id].append(r)
    wins = pairs = 0.0
    for job_rows in by_job.values():
        w, p = _wins(job_rows, order)
        wins, pairs = wins + w, pairs + p
    return round(wins / pairs, 3) if pairs else None


def within_job_pairs(rows: Sequence[PairRow]) -> int:
    by_job: dict[str, list[PairRow]] = defaultdict(list)
    for r in rows:
        by_job[r.job_id].append(r)
    return sum(_wins(job_rows, lambda r: 0.0)[1] for job_rows in by_job.values())


class Interval(BaseModel):
    mean: float
    low: float
    high: float


def bootstrap_difference(
    rows: Sequence[PairRow], *, n: int = 2000, seed: int = 0
) -> Interval | None:
    """Gemini minus v0 within-job measure, resampling jobs (95% interval)."""
    by_job: dict[str, list[PairRow]] = defaultdict(list)
    for r in rows:
        by_job[r.job_id].append(r)
    # Per job: (Gemini wins, v0 wins, fit-vs-no pairs). Jobs without such pairs add nothing.
    per_job = [
        (_wins(rs, gemini_order)[0], _wins(rs, v0_order)[0], _wins(rs, gemini_order)[1])
        for rs in by_job.values()
    ]
    per_job = [t for t in per_job if t[2]]
    if len(per_job) < 2:
        return None
    rng = random.Random(seed)
    diffs: list[float] = []
    for _ in range(n):
        sample = rng.choices(per_job, k=len(per_job))
        pairs = sum(t[2] for t in sample)
        diffs.append((sum(t[0] for t in sample) - sum(t[1] for t in sample)) / pairs)
    diffs.sort()
    return Interval(
        mean=round(sum(diffs) / len(diffs), 3),
        low=round(diffs[int(0.025 * (len(diffs) - 1))], 3),
        high=round(diffs[int(0.975 * (len(diffs) - 1))], 3),
    )


class JobCounts(BaseModel):
    jobs: int
    with_labelled_fit: int
    gemini_nothing_but_fit: int  # Gemini no_fit although a labelled fit exists
    v0_nothing_but_fit: int  # v0 empty shortlist although a labelled fit exists
    without_labelled_fit: int
    gemini_nothing_and_no_fit: int
    v0_nothing_and_no_fit: int


def job_counts(rows: Sequence[PairRow], gemini: dict[str, GeminiJob], v0: V0Run) -> JobCounts:
    jobs = sorted({r.job_id for r in rows})
    has_fit = {j for j in jobs if any(r.job_id == j and r.label == "fit" for r in rows)}

    def g_nothing(j: str) -> bool:
        job = gemini[j]
        return job.no_fit or not any(p.verdict != "no" for p in job.pairs.values())

    def v_nothing(j: str) -> bool:
        return not v0.shortlist(j)

    return JobCounts(
        jobs=len(jobs),
        with_labelled_fit=len(has_fit),
        gemini_nothing_but_fit=sum(g_nothing(j) for j in has_fit),
        v0_nothing_but_fit=sum(v_nothing(j) for j in has_fit),
        without_labelled_fit=len(jobs) - len(has_fit),
        gemini_nothing_and_no_fit=sum(g_nothing(j) for j in jobs if j not in has_fit),
        v0_nothing_and_no_fit=sum(v_nothing(j) for j in jobs if j not in has_fit),
    )


class Comparison(BaseModel):
    v0_version: str
    v0_created_at: datetime | None
    jobs_judged: int
    labelled_pairs: int
    pairs_without_verdict: int
    confusion: dict[str, dict[str, int]]
    exact_agreement: float | None
    kappa: float | None
    kappa_agreed_labels: float | None  # labels where both AI judges agreed
    gemini_surfaced: Surfaced
    v0_surfaced: Surfaced
    v0_surfaced_with_flagged: Surfaced
    within_job_pairs: int
    within_job_gemini: float | None
    within_job_v0_shortlist: float | None
    within_job_difference: Interval | None
    jobs: JobCounts


def compare(
    labels: Sequence[LabelledPair], results: Iterable[TrialResult], v0: V0Run
) -> tuple[Comparison, list[PairRow]]:
    gemini = gemini_jobs(results)
    rows = pair_rows(labels, gemini, v0)
    judged = [r for r in rows if r.gemini_verdict is not None]
    agreed = [r for r in rows if r.source.startswith("ai-agree")]
    return (
        Comparison(
            v0_version=v0.version,
            v0_created_at=v0.created_at,
            jobs_judged=len({r.job_id for r in rows}),
            labelled_pairs=len(rows),
            pairs_without_verdict=len(rows) - len(judged),
            confusion=confusion(rows),
            exact_agreement=(
                round(sum(r.label == r.gemini_verdict for r in judged) / len(judged), 3)
                if judged
                else None
            ),
            kappa=kappa(rows),
            kappa_agreed_labels=kappa(agreed),
            gemini_surfaced=surfaced(rows, "gemini (fit or partial)", lambda r: r.gemini_surfaced),
            v0_surfaced=surfaced(rows, "v0 shortlist", lambda r: r.v0_shortlisted),
            v0_surfaced_with_flagged=surfaced(
                rows, "v0 shortlist + flagged", lambda r: r.v0_shortlisted or r.v0_flagged
            ),
            within_job_pairs=within_job_pairs(rows),
            within_job_gemini=within_job(rows, gemini_order),
            within_job_v0_shortlist=within_job(rows, v0_order),
            within_job_difference=bootstrap_difference(rows),
            jobs=job_counts(rows, gemini, v0)
            if rows
            else JobCounts(
                jobs=0,
                with_labelled_fit=0,
                gemini_nothing_but_fit=0,
                v0_nothing_but_fit=0,
                without_labelled_fit=0,
                gemini_nothing_and_no_fit=0,
                v0_nothing_and_no_fit=0,
            ),  # fmt: skip
        ),
        rows,
    )
