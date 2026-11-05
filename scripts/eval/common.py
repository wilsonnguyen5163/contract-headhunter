"""Shared pieces for the evaluation scripts: today's scores for every job x contractor, the
blind label files, and the ranking measures used to compare a matching method with today's
score (doc/PROTOTYPES.md). Read-only for project data."""

import csv
import os
from collections import Counter
from pathlib import Path

import numpy as np

from jobmatch.config import Settings, load_settings
from jobmatch.match.run import score_all_jobs
from jobmatch.match.scorer import Candidate
from jobmatch.schemas import ContractorProfile, Job


def labels_path(settings: Settings) -> Path:
    """The blind consensus labels by default (doc/LABELLING.md); LABELS=<csv> picks another
    file with job_id, contractor_id and label columns."""
    default = settings.paths.data_dir / "eval" / "blind_consensus.csv"
    return Path(os.environ.get("LABELS", default))


def read_labels(path: Path, pairs: set[tuple[str, str]] | None = None) -> dict:
    """(job_id, contractor_id) -> fit / partial / no; the last row wins for a repeated pair."""
    out: dict[tuple[str, str], str] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            key = (r["job_id"], r["contractor_id"])
            if pairs is None or key in pairs:
                out[key] = r["label"]
    return out


class Adopted:
    """Today's score for every job x contractor (a jobs x contractors matrix), with the
    candidates behind it and the shortlist rule, so another method can be compared."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        scored = score_all_jobs(self.settings)
        self.jobs: dict[str, Job] = {j.job_id: j for j in scored.jobs}
        self.profiles: dict[str, ContractorProfile] = {p.talent_id: p for p in scored.profiles}
        self.J, self.C = list(self.jobs), list(self.profiles)
        self.ji = {k: i for i, k in enumerate(self.J)}
        self.ci = {k: i for i, k in enumerate(self.C)}
        self.cands: dict[tuple[str, str], Candidate] = {
            (j, c.contractor_id): c for j, ranked in scored.ranked.items() for c in ranked
        }
        self.score = np.zeros((len(self.J), len(self.C)))
        for (j, c), cand in self.cands.items():
            self.score[self.ji[j], self.ci[c]] = cand.score

    def shortlist(self, mat: np.ndarray) -> set[tuple[str, str]]:
        """Pairs a score matrix would shortlist with the configured min_score, top_k and gates."""
        m = self.settings.matching
        out: set[tuple[str, str]] = set()
        for j, job_id in enumerate(self.J):
            kept = 0
            for c in np.argsort(-mat[j]):
                if mat[j, c] < m.min_score or kept == m.top_k:
                    break
                if self.cands[(job_id, self.C[c])].gates_failed:
                    continue
                kept += 1
                out.add((job_id, self.C[c]))
        return out


class Metrics:
    """Ranking measures of a score matrix against labels (see doc/PROTOTYPES.md):
    W = within one job, G = across jobs, NM = the job's top score tells jobs with a fit from
    jobs with none."""

    COLUMNS = ("W f>n", "W f>p", "W a>n", "G a>n", "NM")

    def __init__(self, base: Adopted, labels: dict[tuple[str, str], str]) -> None:
        self.base, self.labels = base, labels

    def pairwise(self, mat: np.ndarray, pos: str, neg: str, same_job: bool = True) -> float:
        by: dict[str, list[tuple[float, str]]] = {}
        for (j, c), lab in self.labels.items():
            score = float(mat[self.base.ji[j], self.base.ci[c]])
            by.setdefault(j if same_job else "", []).append((score, lab))
        wins = total = 0.0
        for items in by.values():
            ps = [s for s, lab in items if lab in pos.split("|")]
            ns = [s for s, lab in items if lab in neg.split("|")]
            for a in ps:
                for b in ns:
                    total += 1
                    wins += 1.0 if a > b else 0.5 if a == b else 0.0
        return wins / total if total else float("nan")

    def no_match(self, mat: np.ndarray) -> float:
        has_fit: dict[str, bool] = {}
        for (j, _), lab in self.labels.items():
            has_fit[j] = has_fit.get(j, False) or lab == "fit"
        top = {j: mat[self.base.ji[j]].max() for j in has_fit}
        ps = [top[j] for j, f in has_fit.items() if f]
        ns = [top[j] for j, f in has_fit.items() if not f]
        if not ps or not ns:
            return float("nan")
        wins = sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in ps for b in ns)
        return wins / (len(ps) * len(ns))

    def values(self, mat: np.ndarray) -> list[float]:
        return [
            self.pairwise(mat, "fit", "no"),
            self.pairwise(mat, "fit", "partial"),
            self.pairwise(mat, "fit|partial", "no"),
            self.pairwise(mat, "fit|partial", "no", same_job=False),
            self.no_match(mat),
        ]

    def header(self, width: int = 58, shortlist: bool = True) -> str:
        tail = "   shortlist fit/partial/no/unlabelled" if shortlist else ""
        return f"{'variant':{width}} " + " ".join(f"{c:>6}" for c in self.COLUMNS) + tail

    def row(self, name: str, mat: np.ndarray, shortlist: bool = True, width: int = 58) -> str:
        line = f"{name[:width]:{width}} " + " ".join(f"{v:6.3f}" for v in self.values(mat))
        if shortlist:
            kinds = [self.labels.get(p, "unlabelled") for p in self.base.shortlist(mat)]
            line += "   " + "/".join(
                f"{kinds.count(k):3}" for k in ("fit", "partial", "no", "unlabelled")
            )
        return line

    def paired_bootstrap(
        self, variant: np.ndarray, reference: np.ndarray, rounds: int = 300, seed: int = 0
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        """90% intervals of (variant - reference) over jobs resampled with replacement, for
        W fit>partial and W fit|partial>no. An interval that excludes 0 is a real difference."""
        rng = np.random.default_rng(seed)
        jobs = sorted({j for j, _ in self.labels})
        saved, diffs = self.labels, []
        try:
            for _ in range(rounds):
                pick = set(rng.choice(jobs, size=len(jobs), replace=True))
                self.labels = {k: v for k, v in saved.items() if k[0] in pick}
                diffs.append([
                    self.pairwise(variant, p, n) - self.pairwise(reference, p, n)
                    for p, n in (("fit", "partial"), ("fit|partial", "no"))
                ])  # fmt: skip
        finally:
            self.labels = saved
        lo, hi = np.nanpercentile(np.array(diffs), [5, 95], axis=0)
        return (lo[0], hi[0]), (lo[1], hi[1])


def cross_validate(
    metrics: Metrics,
    variants: dict[str, np.ndarray],
    reference: np.ndarray,
    folds: int = 5,
    repeats: int = 20,
    seed: int = 20261104,
) -> None:
    """Pick the best variant (or the reference) on 4/5 of the jobs, score it on the other
    1/5 against the reference, many times. Objective: mean of W fit>no and G fit>no. Shows
    whether choosing among these variants beats the reference on jobs it wasn't chosen on."""
    options = {"(reference)": reference, **variants}
    saved = metrics.labels
    jobs = sorted({j for j, _ in saved})
    rng = np.random.default_rng(seed)

    def objective(mat: np.ndarray) -> float:
        return float(np.nanmean([metrics.pairwise(mat, "fit", "no"),
                                 metrics.pairwise(mat, "fit", "no", same_job=False)]))  # fmt: skip

    diffs, picks = [], Counter()
    try:
        for _ in range(repeats):
            perm = rng.permutation(jobs)
            for f in range(folds):
                test = set(perm[f::folds])
                metrics.labels = {k: v for k, v in saved.items() if k[0] not in test}
                best = max(options, key=lambda n: objective(options[n]))
                picks[best] += 1
                metrics.labels = {k: v for k, v in saved.items() if k[0] in test}
                diffs.append(objective(options[best]) - objective(reference))
    finally:
        metrics.labels = saved
    d = np.array([x for x in diffs if not np.isnan(x)])
    print(
        f"\nCross-validation ({repeats} x {folds} folds by job), held-out objective of the "
        f"train-best variant minus the reference: mean {d.mean():+.3f}, better in "
        f"{np.mean(d > 0):.0%}, worse in {np.mean(d < 0):.0%}, equal in {np.mean(d == 0):.0%}"
    )
    print(
        "Picked on the training folds: " + ", ".join(f"{n} {k}x" for n, k in picks.most_common(6))
    )
