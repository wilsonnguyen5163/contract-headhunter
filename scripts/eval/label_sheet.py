"""Blind labelling sheet: judges label job x contractor pairs without seeing any score,
band, rank, method or earlier label. See doc/LABELLING.md.

Make a sheet (exports/label-sheet-<time>.csv for the judges, and a key they must not see in
data/eval/label-sheets/):
  docker compose run --rm -v ./scripts/eval:/exp:ro app python /exp/label_sheet.py make

Import a filled sheet into data/eval/blind_labels.csv (append-only, one row per judge),
rebuild data/eval/blind_consensus.csv (one label per pair) and show agreement between judges:
  docker compose run --rm -v ./scripts/eval:/exp:ro app \
      python /exp/label_sheet.py import exports/label-sheet-<time>.csv [--dry-run]

Pairs already labelled (data/eval/blind_labels.csv) are skipped. The rest are chosen where
they decide something: contractors no sheet has covered yet, each band of today's
shortlists, just below the cut-off (missed fits), plus a few at random. When a second
matching method exists (v1: an LLM judge), add the pairs it and today's score disagree on
(see `alternatives` in make()). Sampling uses a fixed seed, so the same data gives the same
sheet. The sheet has no contractor names, so it can be pasted into AI tools as it is.
"""

import csv
import os
import random
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from common import Adopted, read_labels
from pydantic import BaseModel

from jobmatch.storage.csv_store import export_csv

SEED = 20261104
NEW_CONTRACTORS = 50  # pairs with contractors no sheet has covered yet
MAX_DISAGREEMENT = 45  # pairs today's score and an alternative method disagree on
PER_BAND = 10  # today's shortlist, per band
NEAR_MISS = 15  # ranked 6-10
RANDOM = 10  # anywhere, mostly to check "no" is really no
MAX_PER_JOB = 5
LABELS = {
    "fit": "fit", "f": "fit", "partial": "partial", "p": "partial",
    "no": "no", "n": "no", "unfit": "no",
}  # fmt: skip


class SheetRow(BaseModel):
    """What the judges see: the job, the contractor (no name), and a label and reason
    column per judge. A person who judges alone can use human_label and reason_human."""

    item: int
    job_title: str
    company: str | None
    job_location: str | None
    job_url: str
    job_summary: str
    contractor_id: str
    headline: str | None
    target_titles: str
    seniority: str | None
    contractor_location: str | None
    core_strengths: str
    industries: str
    rate: str | None
    human_label: str = ""  # fit / partial / no (or unfit)
    reason_human: str = ""
    chatgpt_label: str = ""
    chatgpt_reasoning: str = ""
    claude_label: str = ""
    claude_reasoning: str = ""


class KeyRow(BaseModel):
    """Kept away from the judges: which pair each item is and why it was sampled."""

    item: int
    job_id: str
    contractor_id: str
    sampled_as: str


def _already_labelled(path: Path) -> set[tuple[str, str]]:
    return set(read_labels(path)) if path.exists() else set()


def make() -> None:
    base = Adopted()
    s = base.settings
    rng = random.Random(SEED)
    done = _already_labelled(s.paths.data_dir / "eval" / "blind_labels.csv")
    covered = {c for _, c in done}
    new = [c for c in base.C if c not in covered]
    print(f"{len(done)} pairs already labelled; {len(new)} contractor(s) on no sheet yet")

    # Shortlists of alternative matching methods, by name. Empty since the v0 prototypes
    # ended (doc/PROTOTYPES.md); in v1, add the LLM judge's shortlist here so the sheet
    # samples the pairs it and today's score disagree on.
    alternatives: dict[str, set[tuple[str, str]]] = {}
    adopted = base.shortlist(base.score)

    reasons: dict[tuple[str, str], list[str]] = defaultdict(list)
    per_job: Counter = Counter()

    def take(pairs: list[tuple[str, str]], why: str, limit: int) -> None:
        pairs = sorted(p for p in set(pairs) if p not in done)
        rng.shuffle(pairs)
        taken = 0
        for pair in pairs:
            if taken == limit:
                break
            if pair in reasons:
                reasons[pair].append(why)
                continue
            if per_job[pair[0]] >= MAX_PER_JOB:
                continue
            reasons[pair].append(why)
            per_job[pair[0]] += 1
            taken += 1

    def ranked(j: str) -> list[str]:
        return [c.contractor_id for c in sorted((base.cands[(j, k)] for k in base.C),
                                                key=lambda c: -c.score)]  # fmt: skip

    # 1. Contractors no sheet has covered: their best-ranked jobs first, then any job.
    top_new = [(j, c) for j in base.J for c in ranked(j)[:10] if c in new]
    take(top_new, "new contractor", NEW_CONTRACTORS * 4 // 5)
    take([(j, c) for j in base.J for c in new], "new contractor", NEW_CONTRACTORS // 5)
    # 2. Disagreements between today's score and an alternative method.
    if alternatives:
        every = (*alternatives.values(), adopted)
        disagree = [p for p in set().union(*every) if sum(p in v for v in every) < len(every)]
        take(disagree, "methods disagree", MAX_DISAGREEMENT)
    # 3. Today's shortlist, per band (are the bands honest?).
    for band in ("high", "medium", "low"):
        take(
            [p for p in adopted if base.cands[p].band.value == band], f"shortlist {band}", PER_BAND
        )
    # 4. Near misses (fits the cut-off loses?).
    near = [(j, c) for j in base.J for c in ranked(j)[5:10]
            if not base.cands[(j, c)].gates_failed and (j, c) not in adopted]  # fmt: skip
    take(near, "near miss", NEAR_MISS)
    # 5. A few at random.
    take([(j, c) for j in base.J for c in base.C], "random", RANDOM)

    sheet_path, key_path, n_jobs = write_sheet(base, reasons, rng)
    print(f"Wrote {len(reasons)} pairs from {n_jobs} jobs to {sheet_path}")
    print(f"Key (do not give to the judges): {key_path}")
    why = Counter(r for rs in reasons.values() for r in rs)
    on_new = sum(1 for _, c in reasons if c in new)
    print("Sampled as: " + ", ".join(f"{k} {v}" for k, v in why.items()))
    print(f"Pairs with contractors on no earlier sheet: {on_new}")
    for k, v in alternatives.items():
        print(f"{k} vs today's shortlists: {len(v - adopted)} added, {len(adopted - v)} dropped")


def write_sheet(
    base: Adopted, reasons: dict[tuple[str, str], list[str]], rng: random.Random
) -> tuple[Path, Path, int]:
    """Write the judges' sheet (exports/) and its key (data/eval/label-sheets/) for these
    pairs. Grouped by job so a judge reads each posting once; random order otherwise.
    Returns the two paths and the number of jobs."""
    s = base.settings
    jobs = sorted({j for j, _ in reasons})
    rng.shuffle(jobs)
    order = [
        pair for j in jobs for pair in rng.sample(
            sorted(p for p in reasons if p[0] == j), k=sum(1 for p in reasons if p[0] == j)
        )
    ]  # fmt: skip
    sheet, key = [], []
    for item, (j, c) in enumerate(order, start=1):
        job, p = base.jobs[j], base.profiles[c]
        # The whole cleaned posting; line breaks kept so lists stay readable in Excel.
        text = job.description_clean or job.description_raw or ""
        lines = (" ".join(line.split()) for line in text.splitlines())
        summary = "\n".join(line for line in lines if line)
        sheet.append(
            SheetRow(
                item=item,
                job_title=job.title_raw,
                company=job.company_raw,
                job_location=job.location_raw,
                job_url=job.url,
                job_summary=summary,
                contractor_id=c,
                headline=p.standardized_headline,
                target_titles="; ".join(p.target_titles),
                seniority=p.seniority,
                contractor_location=p.location,
                core_strengths="; ".join(p.core_strengths),
                industries="; ".join(p.main_industry),
                rate=p.rate_expectation,
            )
        )
        key.append(
            KeyRow(item=item, job_id=j, contractor_id=c, sampled_as="|".join(reasons[(j, c)]))
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    sheet_path = s.paths.exports_dir / f"label-sheet-{stamp}.csv"
    key_path = s.paths.data_dir / "eval" / "label-sheets" / f"label-sheet-{stamp}.key.csv"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    export_csv(SheetRow, sheet, sheet_path)
    export_csv(KeyRow, key, key_path)
    return sheet_path, key_path, len(jobs)


def _read(path: Path) -> list[dict[str, str]]:
    for encoding in ("utf-8-sig", "cp1252"):  # Excel on Windows may save as cp1252
        try:
            with path.open(encoding=encoding, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"{path}: cannot read the file (save it as CSV UTF-8)")


# Judge columns a returned sheet may carry instead of (or as well as) label/reason/reviewer:
# judge name -> (label column, reason column). Matched case-insensitively.
# Headers are compared normalized (_header): "reason (Human)" and "reason_human" both work.
JUDGE_COLUMNS = {
    "human": ("human label", "reason human"),
    "chatgpt": ("chatgpt label", "chatgpt reasoning"),
    "claude-web": ("claude label", "claude reasoning"),
}
HUMAN_JUDGES = {"human"}  # a human label overrides the AI judges in the consensus
CONSENSUS_HEADER = ["job_id", "contractor_id", "label", "source", "reason"]


def _kappa(pairs: list[tuple[str, str]]) -> tuple[float, float]:
    """Exact agreement and Cohen's kappa."""
    n = len(pairs)
    agree = sum(a == b for a, b in pairs) / n
    pa, pb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    chance = sum(pa[k] * pb[k] for k in set(pa) | set(pb)) / n**2
    return agree, (agree - chance) / (1 - chance) if chance < 1 else float("nan")


def _header(name: str) -> str:
    return " ".join(re.sub(r"[_()]", " ", name).casefold().split())


def _sheet_judges(row: dict[str, str]) -> dict[str, tuple[str, str]]:
    """judge -> (label column, reason column) found in this sheet's header."""
    header = {_header(h): h for h in row}
    found = {
        judge: (header[label], header.get(reason, ""))
        for judge, (label, reason) in JUDGE_COLUMNS.items()
        if label in header
    }
    if "label" in header:
        found[""] = (header["label"], header.get("reason", ""))  # judge from the reviewer cell
    return found


def write_consensus(labels_path: Path, out_path: Path) -> Counter:
    """One label per pair: the human's when there is one, else the AI judges' when they agree,
    else partial (they disagree). One AI saying fit and the other partial counts as fit
    ("ai-fit-partial"): user decision 2026-11-04, after sheet 2, where ChatGPT judged fit/unfit
    only and Claude used partial for "worth considering". Derived from blind_labels.csv;
    rebuilt on every import, the previous file backed up first."""
    by_pair: dict[tuple[str, str], dict[str, tuple[str, str]]] = defaultdict(dict)
    for r in _read(labels_path):
        by_pair[(r["job_id"], r["contractor_id"])][r["judge"]] = (r["label"], r["reason"])
    rows, sources = [], Counter()
    for (j, c), judges in sorted(by_pair.items()):
        human = [v for k, v in judges.items() if k in HUMAN_JUDGES or k not in JUDGE_COLUMNS]
        ai = {k: v for k, v in judges.items() if k in JUDGE_COLUMNS and k not in HUMAN_JUDGES}
        if human:
            label, reason = human[-1]
            source = "human"
        elif len({lab for lab, _ in ai.values()}) == 1:
            label = next(iter(ai.values()))[0]
            source = "ai-agree" if len(ai) > 1 else "ai-single"
            reason = " | ".join(f"{k}: {r}" for k, (_, r) in ai.items())
        elif {lab for lab, _ in ai.values()} == {"fit", "partial"}:
            label, source = "fit", "ai-fit-partial"
            reason = " | ".join(f"{k} ({lab}): {r}" for k, (lab, r) in ai.items())
        else:
            label, source = "partial", "ai-disagree"
            reason = " | ".join(f"{k} ({lab}): {r}" for k, (lab, r) in ai.items())
        rows.append([j, c, label, source, reason])
        sources[(source, label)] += 1
    if out_path.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = out_path.parent / "backups" / f"{out_path.stem}.{stamp}.csv"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out_path, backup)
    tmp = out_path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(CONSENSUS_HEADER)
        writer.writerows(rows)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)
    return sources


def import_sheet(sheet_path: Path, dry_run: bool = False) -> None:
    from jobmatch.config import load_settings

    s = load_settings()
    match = re.search(r"\d{8}T\d{6}Z", sheet_path.stem)
    if not match:
        raise SystemExit(f"{sheet_path.name}: the file name must keep the sheet's time stamp")
    stamp = match.group()
    key_path = s.paths.data_dir / "eval" / "label-sheets" / f"label-sheet-{stamp}.key.csv"
    if not key_path.exists():
        raise SystemExit(f"No key for this sheet: {key_path}")
    key = {r["item"]: r for r in _read(key_path)}
    out_path = s.paths.data_dir / "eval" / "blind_labels.csv"
    existing = set()
    if out_path.exists():
        existing = {(r["job_id"], r["contractor_id"], r["judge"]) for r in _read(out_path)}

    sheet = _read(sheet_path)
    judges = _sheet_judges(sheet[0]) if sheet else {}
    if not judges:
        raise SystemExit("No label column found (label, or one of: "
                         + ", ".join(c for c, _ in JUDGE_COLUMNS.values()) + ")")  # fmt: skip
    rows, problems, blank = [], [], Counter()
    by_judge: dict[str, dict[tuple[str, str], str]] = defaultdict(dict)
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    for r in sheet:
        item = (r.get("item") or "").strip()
        for fixed_judge, (label_col, reason_col) in judges.items():
            judge = fixed_judge or (r.get("reviewer") or "").strip() or "reviewer"
            raw = (r.get(label_col) or "").strip().lower()
            if not raw:
                blank[judge] += 1
                continue
            if item not in key:
                problems.append(f"item {item}: not in the key")
                continue
            if raw not in LABELS:
                problems.append(f"item {item} ({judge}): label {raw!r} is not fit, partial or no")
                continue
            k = key[item]
            by_judge[judge][(k["job_id"], k["contractor_id"])] = LABELS[raw]
            if (k["job_id"], k["contractor_id"], judge) in existing:
                continue
            reason = (r.get(reason_col) or "").strip() if reason_col else ""
            rows.append([k["job_id"], k["contractor_id"], LABELS[raw], reason, judge, now,
                         stamp, k["sampled_as"]])  # fmt: skip
    for p in problems:
        print("  " + p)
    if problems:
        raise SystemExit(f"{len(problems)} problem(s): fix the sheet and import again")

    for judge, labels in by_judge.items():
        print(f"  {judge}: {len(labels)} label(s) {dict(Counter(labels.values()))}, "
              f"{blank[judge]} blank")  # fmt: skip
    if dry_run:
        print(f"Dry run: {len(rows)} label(s) would be imported; nothing written")
    elif rows:
        new_file = not out_path.exists()
        with out_path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            if new_file:
                writer.writerow(["job_id", "contractor_id", "label", "reason", "judge",
                                 "judged_at", "sheet", "sampled_as"])  # fmt: skip
            writer.writerows(rows)
        print(f"Imported {len(rows)} label(s) into {out_path}")
    else:
        print("Nothing new to import")
    if not dry_run and out_path.exists():
        consensus_path = s.paths.data_dir / "eval" / "blind_consensus.csv"
        sources = write_consensus(out_path, consensus_path)
        counts = ", ".join(f"{src} {lab} {n}" for (src, lab), n in sorted(sources.items()))
        print(f"Consensus labels in {consensus_path}: {counts}")

    # Agreement between judges, and with Claude Code's earlier (non-blind) labels.
    claude = read_labels(s.paths.data_dir / "eval" / "llm_judge_labels.csv")
    others = {**by_judge, "earlier labels (not blind)": claude}
    names = list(others)
    print("\nAgreement on shared pairs (exact, Cohen's kappa):")
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            shared = [(others[a][p], others[b][p]) for p in others[a] if p in others[b]]
            if shared:
                agree, kappa = _kappa(shared)
                print(f"  {a} vs {b}: {len(shared)} pairs, {agree:.0%}, kappa {kappa:.2f}")
    print("\nRe-run the comparisons with LABELS=data/eval/blind_consensus.csv")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "make":
        make()
    elif len(sys.argv) in (3, 4) and sys.argv[1] == "import":
        import_sheet(Path(sys.argv[2]), dry_run="--dry-run" in sys.argv[3:])
    else:
        raise SystemExit(__doc__)
