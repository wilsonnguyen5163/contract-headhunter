"""Re-fit the score weights and thresholds on the labels, with cross-validation by job.
Read-only: settings are changed in memory only; nothing is written.

  docker compose run --rm -e LABELS=data/eval/blind_consensus.csv \\
      -v ./scripts/eval:/exp:ro app python /exp/tune.py

1. Every weight combination in GRID is scored through the real scorer (score_all_jobs).
2. Objective: mean of W fit>no (order within a job) and G fit>no (order across jobs, which
   min_score and the bands rely on). Partial pairs are left out of the objective.
3. Cross-validation: jobs are split into 5 folds, 20 times. Each time the best combination
   on 4 folds is checked on the 5th against today's weights on the same jobs. A change is
   worth adopting only if it wins on held-out jobs, not just on all the labels at once.
4. For the winner (and today's weights): fit rate per score range, to set min_score and
   the bands; shortlist counts; and the label reasons of the worst mistakes.
"""

import csv
import itertools
import time
from collections import Counter

import numpy as np
from common import labels_path

from jobmatch.config import Settings, load_settings
from jobmatch.match.run import score_all_jobs

GRID = {
    "cosine": [0.30, 0.45, 0.60],
    "title": [0.15, 0.30, 0.45],
    "skill_overlap": [0.0, 0.10, 0.25],
    "field": [0.0, 0.10, 0.20, 0.30],
    "lexical": [0.0, 0.10, 0.20, 0.30],
}
FOLDS, REPEATS, SEED = 5, 20, 20261104

settings = load_settings()
path = labels_path(settings)
with path.open(encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))
labels = {(r["job_id"], r["contractor_id"]): r["label"] for r in rows}
reasons = {(r["job_id"], r["contractor_id"]): r.get("reason", "") for r in rows}
print(f"Labels: {path.name}, {len(labels)} pairs {dict(Counter(labels.values()))}", flush=True)


def with_weights(s: Settings, **weights: float) -> Settings:
    m = s.matching
    return s.model_copy(
        update={"matching": m.model_copy(update={"weights": m.weights.model_copy(update=weights)})}
    )


Run = dict  # (job_id, contractor_id) -> (score, band, gates_failed); plus "_ranked"


def score(s: Settings) -> Run:
    scored = score_all_jobs(s)
    out: Run = {}
    for j, ranked in scored.ranked.items():
        for c in ranked:
            out[(j, c.contractor_id)] = (c.score, c.band.value, bool(c.gates_failed))
    out["_ranked"] = scored.ranked
    return out


def auc(run: Run, jobs: set[str], same_job: bool) -> float:
    groups: dict[str, list[tuple[float, str]]] = {}
    for (j, c), lab in labels.items():
        if j in jobs and lab in ("fit", "no") and (j, c) in run:
            groups.setdefault(j if same_job else "", []).append((run[(j, c)][0], lab))
    wins = total = 0.0
    for items in groups.values():
        ps = [s for s, lab in items if lab == "fit"]
        ns = [s for s, lab in items if lab == "no"]
        for a in ps:
            for b in ns:
                total += 1
                wins += 1.0 if a > b else 0.5 if a == b else 0.0
    return wins / total if total else float("nan")


def objective(run: Run, jobs: set[str]) -> float:
    return float(np.nanmean([auc(run, jobs, True), auc(run, jobs, False)]))


def shortlist(run: Run, s: Settings) -> Counter:
    m = s.matching
    out: Counter = Counter()
    for j, ranked in run["_ranked"].items():
        kept = 0
        for c in sorted(ranked, key=lambda c: -run[(j, c.contractor_id)][0]):
            sc, _, gated = run[(j, c.contractor_id)]
            if sc < m.min_score or kept == m.top_k:
                break
            if gated:
                continue
            kept += 1
            out[labels.get((j, c.contractor_id), "unlabelled")] += 1
    return out


# ---- 1. score every combination ----
current = {k: getattr(settings.matching.weights, k) for k in GRID}
combos = [dict(zip(GRID, v, strict=True)) for v in itertools.product(*GRID.values())]
combos = [c for c in combos if c["cosine"] + c["title"] > 0]
if current not in combos:
    combos.append(current)
t0 = time.time()
runs: list[Run] = []
for i, c in enumerate(combos):
    runs.append(score(with_weights(settings, **c)))
    if i == 0:
        print(
            f"Scoring {len(combos)} combinations, about {time.time() - t0:.1f} s each", flush=True
        )
cur = combos.index(current)
all_jobs = {j for j, _ in labels}
print(f"Scored in {time.time() - t0:.0f} s")


def name(c: dict) -> str:
    return " ".join(f"{k[:5]} {v:.2f}" for k, v in c.items())


def describe(run: Run, s: Settings) -> str:
    sl = shortlist(run, s)
    return (
        f"W f>n {auc(run, all_jobs, True):.3f}  G f>n {auc(run, all_jobs, False):.3f}  "
        f"shortlist fit/partial/no/unlabelled {sl['fit']}/{sl['partial']}/{sl['no']}/"
        f"{sl['unlabelled']}"
    )


scores = [objective(r, all_jobs) for r in runs]
order = np.argsort(scores)[::-1]
print(f"\nToday's weights ({name(current)}): {describe(runs[cur], settings)}")
print("Best 10 on all labels (in-sample, optimistic):")
for i in order[:10]:
    print(f"  {scores[i]:.3f}  {name(combos[i])}   {describe(runs[i], settings)}")

# ---- 2. cross-validation by job ----
rng = np.random.default_rng(SEED)
jobs_list = sorted(all_jobs)
diffs, picks = [], Counter()
for _ in range(REPEATS):
    perm = rng.permutation(jobs_list)
    for f in range(FOLDS):
        test = set(perm[f::FOLDS])
        train = all_jobs - test
        best = max(range(len(combos)), key=lambda i: objective(runs[i], train))
        picks[best] += 1
        diffs.append(objective(runs[best], test) - objective(runs[cur], test))
d = np.array([x for x in diffs if not np.isnan(x)])
print(
    f"\nCross-validation ({REPEATS} x {FOLDS} folds by job): held-out objective of the "
    f"train-best weights minus today's: mean {d.mean():+.3f}, median {np.median(d):+.3f}, "
    f"better in {np.mean(d > 0):.0%}, worse in {np.mean(d < 0):.0%} of folds"
)
print("Weights most often picked on the training folds:")
for i, n in picks.most_common(5):
    print(f"  {n:3}x  {name(combos[i])}")

# A stable choice: the most-picked combination, unless today's does as well.
winner = picks.most_common(1)[0][0]


# ---- 3. thresholds ----
def fit_rates(run: Run) -> None:
    edges = [0.0, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 1.01]
    print("  score range   fit  partial  no   fit rate")
    for lo, hi in itertools.pairwise(edges):
        labs = [lab for p, lab in labels.items() if p in run and lo <= run[p][0] < hi]
        if labs:
            n = Counter(labs)
            print(
                f"  {lo:.2f}-{min(hi, 1):.2f}    {n['fit']:4} {n['partial']:6} {n['no']:4}   "
                f"{n['fit'] / len(labs):5.0%}"
            )


print(f"\nFit rate by score, today's weights ({name(current)}):")
fit_rates(runs[cur])
if winner != cur:
    print(f"\nFit rate by score, most-picked weights ({name(combos[winner])}):")
    fit_rates(runs[winner])
    print(f"  {describe(runs[winner], settings)}")


# ---- 4. the worst mistakes and what the labels' reasons say ----
def mistakes(run: Run, s: Settings, k: int = 8) -> None:
    labelled = [(p, lab, run[p][0]) for p, lab in labels.items() if p in run]
    false_pos = sorted((x for x in labelled if x[1] == "no"), key=lambda x: -x[2])[:k]
    false_neg = sorted((x for x in labelled if x[1] == "fit"), key=lambda x: x[2])[:k]
    for title, items in (("Highest-scored 'no'", false_pos), ("Lowest-scored 'fit'", false_neg)):
        print(f"  {title}:")
        for (j, c), _, sc in items:
            reason = " ".join(reasons.get((j, c), "").split())[:220]
            print(f"    {sc:.3f} {j} {c}: {reason}")


print(f"\nWorst mistakes with the most-picked weights ({name(combos[winner])}):")
mistakes(runs[winner], settings)
