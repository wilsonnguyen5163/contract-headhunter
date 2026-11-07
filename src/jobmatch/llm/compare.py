"""Compare Gemini's verdicts with the blind labels (data/eval/blind_consensus.csv).

v0 comparisons were removed with the v0 matcher (2026-11-06); the phase-5 results that
compared the two are recorded in doc/LOG-UPDATE.md.

Measures, on labelled pairs of the jobs Gemini judged:
- agreement: Gemini verdict vs label (confusion table, exact agreement, Cohen's kappa);
- surfaced: what Gemini puts in front of the reviewer (fit or partial), split by label;
- within-job ranking: chance that a labelled fit ranks above a labelled "no" in the same job
  (ties count half);
- jobs: "nothing to show" (no fit or partial) vs whether a labelled fit exists.
"""

import csv
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from jobmatch.llm.trial import TrialResult

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

    @property
    def gemini_surfaced(self) -> bool:
        return self.gemini_verdict in ("fit", "partial")


def pair_rows(labels: Iterable[LabelledPair], gemini: dict[str, GeminiJob]) -> list[PairRow]:
    rows: list[PairRow] = []
    for lp in labels:
        job = gemini.get(lp.job_id)
        if job is None:
            continue  # job not judged by Gemini (excluded, or not run yet)
        g = job.pairs.get(lp.contractor_id)
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
    """What Gemini put in front of the reviewer, among labelled pairs, by label."""

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


def _wins(rows: Sequence[PairRow], order: Callable[[PairRow], float]) -> tuple[float, int]:
    wins, pairs = 0.0, 0
    for fit in (r for r in rows if r.label == "fit"):
        for no in (r for r in rows if r.label == "no"):
            a, b = order(fit), order(no)
            wins += 1.0 if a > b else 0.5 if a == b else 0.0
            pairs += 1
    return wins, pairs


def _by_job(rows: Sequence[PairRow]) -> dict[str, list[PairRow]]:
    by_job: dict[str, list[PairRow]] = defaultdict(list)
    for r in rows:
        by_job[r.job_id].append(r)
    return by_job


def within_job(rows: Sequence[PairRow], order: Callable[[PairRow], float]) -> float | None:
    wins = pairs = 0.0
    for job_rows in _by_job(rows).values():
        w, p = _wins(job_rows, order)
        wins, pairs = wins + w, pairs + p
    return round(wins / pairs, 3) if pairs else None


def within_job_pairs(rows: Sequence[PairRow]) -> int:
    return sum(_wins(job_rows, lambda r: 0.0)[1] for job_rows in _by_job(rows).values())


class JobCounts(BaseModel):
    jobs: int
    with_labelled_fit: int
    gemini_nothing_but_fit: int  # Gemini shows nobody although a labelled fit exists
    without_labelled_fit: int
    gemini_nothing_and_no_fit: int


def job_counts(rows: Sequence[PairRow], gemini: dict[str, GeminiJob]) -> JobCounts:
    jobs = sorted({r.job_id for r in rows})
    has_fit = {j for j in jobs if any(r.job_id == j and r.label == "fit" for r in rows)}

    def nothing(j: str) -> bool:
        job = gemini[j]
        return job.no_fit or not any(p.verdict != "no" for p in job.pairs.values())

    return JobCounts(
        jobs=len(jobs),
        with_labelled_fit=len(has_fit),
        gemini_nothing_but_fit=sum(nothing(j) for j in has_fit),
        without_labelled_fit=len(jobs) - len(has_fit),
        gemini_nothing_and_no_fit=sum(nothing(j) for j in jobs if j not in has_fit),
    )


class Comparison(BaseModel):
    jobs_judged: int
    labelled_pairs: int
    pairs_without_verdict: int
    confusion: dict[str, dict[str, int]]
    exact_agreement: float | None
    kappa: float | None
    kappa_agreed_labels: float | None  # labels where both AI judges agreed
    gemini_surfaced: Surfaced
    within_job_pairs: int
    within_job_gemini: float | None
    jobs: JobCounts


def compare(
    labels: Sequence[LabelledPair], results: Iterable[TrialResult]
) -> tuple[Comparison, list[PairRow]]:
    gemini = gemini_jobs(results)
    rows = pair_rows(labels, gemini)
    judged = [r for r in rows if r.gemini_verdict is not None]
    agreed = [r for r in rows if r.source.startswith("ai-agree")]
    return (
        Comparison(
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
            within_job_pairs=within_job_pairs(rows),
            within_job_gemini=within_job(rows, gemini_order),
            jobs=job_counts(rows, gemini),
        ),
        rows,
    )
