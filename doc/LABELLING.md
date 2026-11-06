# Blind labelling sheets

## Why we need them

The matcher's settings (what counts in the score, how much each part weighs, where "high" and "medium" start) need labels made **without seeing any score**.
- **Sheet 1** (`label-sheet-20261103T015024Z`, 155 pairs) came back on 2026-11-04. A person labelled 13 pairs, and ChatGPT and Claude labelled all 155. The person agreed with both AIs on all 13 pairs, and the two AIs agreed with each other on 89%.
- **What it showed:** today's settings hold up, and re-fitting the weights didn't beat them. See [PROTOTYPES.md](PROTOTYPES.md).
- **Why it isn't enough:** every open question ended "within noise", because 155 pairs hold only 40 fits.

**Sheet 2** (`label-sheet-20261104T021127Z`, 150 pairs) adds evidence on:
- **The 11 contractors added on 2026-11-03.** No sheet covered them; 82 of the 150 pairs involve them.
- **The raw-text vector:** a second vector of the raw posting, added to the score?
- **The profession check:** should a "different profession" lower the score, or only show as a note?
- **Strength × industry sentences** as an extra score part?
- **The bands:** are "high", "medium" and "low" honest?

**Sheet 3** (`label-sheet-20261104T040703Z`, 26 pairs) tests the learned score (option 3, [PROTOTYPES.md](PROTOTYPES.md)) directly:
- **8 pairs** only the learned score puts in shortlists. Does it bring in fits?
- **3 pairs** only today's score shortlists. Does it drop non-fits?
- **15 pairs** both shortlist, as a reference.

Every pair was unlabelled. It was made by the learned-score prototype (since removed), and has the same columns as sheet 2. Claude's labels are imported; ChatGPT's are still to come, and can be imported into the same file later.

## What's in the sheet

`exports/label-sheet-<time>.csv`, grouped by job so each posting is read once. Pairs already labelled on an earlier sheet are left out. Sheet 2's pairs were picked like this:

| Why a pair is there | Sheet 2 |
|---|---|
| A contractor no earlier sheet covered | 50 |
| The candidate scoring methods disagree on it | 49 |
| It's on today's shortlist (high, medium and low bands) | 36 |
| It just missed the shortlist (ranked 6–10) | 16 |
| Picked at random | 10 |

The counts add up to more than 150 because some pairs were picked for two reasons.

Judges see none of that: no scores, bands, ranks, profession notes or reasons, and the order is random apart from grouping by job. Some pairs will be obvious non-fits; that's on purpose.

**From sheet 2 on, the sheet has no contractor names.** It shows the contractor ID, headline, target titles, seniority, location, core strengths, industries and rate. So it can be pasted into an AI tool as it is. The job postings are public, but the profiles are still our information. Keep the sheet within the team and the tools the team has agreed to use.

## How to fill it in

Each judge fills their own pair of columns:

| Judge | Label column | Reason column |
|---|---|---|
| A person | `human_label` | `reason_human` |
| ChatGPT | `chatgpt_label` | `chatgpt_reasoning` |
| Claude | `claude_label` | `claude_reasoning` |

**Labels:** `fit`, `partial` or `no` (`unfit`, `f`, `p` and `n` also work). Ask: *would I put this person forward for this job?*
- **fit:** yes, as they are.
- **partial:** maybe. Related experience, but something important is off: seniority, part of the skill set, sector or rate.
- **no:** no, this isn't their kind of work.

**Things to keep in mind:**
- **Judge from the job and the profile only.**
- **Don't count availability or whether the posting is still open.**
- **Leave a cell blank if you can't decide.** Blank cells are skipped. A half-done sheet is still useful.
- **The pairs where ChatGPT and Claude disagree are yours to decide.** After both AIs have labelled, filter the rows where `chatgpt_label` ≠ `claude_label` and fill in `human_label` for them. Your label always wins. Pairs nobody settles count as `partial`.

**Sheet 2 lessons:**
- **ChatGPT used its old prompt:** fit/unfit only, and its answers landed one column to the left. Paste the instruction text below verbatim, and check that each judge's label sits in its own `_label` column before importing.
- **Save in UTF-8:** a save through Excel's Windows encoding turned "—" into "â€”".

Save as **CSV (UTF-8)**. Keep the time stamp in the file name, because it links the sheet to its key. A suffix such as `_labeled` is fine.

### Instructions to give the AI judges

Paste this with the sheet, unchanged, so every round is judged the same way:

> You are labelling job–contractor pairs for a recruiting agency that places contractors in Canadian contract roles. Each row is one job posting and one contractor profile. Judge only from the text in the row.
>
> For each row, decide: would a recruiter put this contractor forward for this job as they are?
> - fit: yes. The contractor's profession matches what the job is accountable for, and their strengths cover the job's core responsibilities.
> - partial: maybe. Related work, but something important is off: seniority, part of the core skill set, or the sector.
> - no: no. A different profession, even if the domain or vocabulary overlaps (for example UX design for a UX writing job, sales for a recruiting job, operations for an HR job).
>
> Rules: credit only skills stated in the profile; don't assume unlisted ones. Ignore availability, location, whether the posting is still open, and the rate unless it is clearly incompatible.
>
> Fill `<your>_label` with fit, partial or no. Fill `<your>_reasoning` with 2–4 sentences: the job's core profession, the contractor's profession, the deciding factor. End with "Reason tag: " and one of: Domain and skill match · Core skill match · Seniority mismatch · Adjacent but missing a required skill · Different field · Title-only match.

## Importing it (for whoever runs jobmatch)

```
# check first: validates every label, shows agreement between judges, writes nothing
docker compose run --rm -v ./scripts/eval:/exp:ro app \
    python /exp/label_sheet.py import exports/label-sheet-<time>_labeled.csv --dry-run
# then import
docker compose run --rm -v ./scripts/eval:/exp:ro app \
    python /exp/label_sheet.py import exports/label-sheet-<time>_labeled.csv
```

- **Where labels go:** `data/eval/blind_labels.csv`, one row per judge with their reason. It's append-only, and re-importing the same sheet adds nothing.
- **Column names:**
  - The import reads the judge columns of sheet 2.
  - It also reads sheet 1's headers (`human label`, `reason (Human)`, `ChatGPT Label`, …) and the older `label` / `reason` / `reviewer` columns.
  - "unfit" counts as "no".
- **The consensus:** every import rebuilds `data/eval/blind_consensus.csv`, which has one label per pair. It takes, in order:
  1. the human's label when there is one;
  2. else the AI judges' label when they agree;
  3. else `fit` when one AI says fit and the other partial (user decision 2026-11-04);
  4. else `partial`.

  The previous file is backed up first. This is the file to tune on.
- **The key:** `data/eval/label-sheets/label-sheet-<time>.key.csv` maps each row to its pair. Don't give it to the judges.
- **The import report** shows agreement between judges and with Claude Code's earlier non-blind labels, as a percentage and Cohen's kappa. Kappa runs from 0 (chance) to 1 (perfect).

Then re-check today's weights on the consensus labels (the default; about 3 minutes):

```
docker compose run --rm -v ./scripts/eval:/exp:ro app python /exp/tune.py
```

To compare another matching method (v1: an LLM judge), build its jobs × contractors score matrix and use `Metrics`, `paired_bootstrap` and `cross_validate` in `scripts/eval/common.py`.

## Making a new sheet

```
docker compose run --rm -v ./scripts/eval:/exp:ro app python /exp/label_sheet.py make
```

It samples contractors no sheet has covered yet, each band of today's shortlists, near misses and a few random pairs. When a second matching method exists, add its shortlist to `alternatives` in `make()`, and the sheet also samples the pairs where the two disagree. With the same data you get the same sheet (fixed seed). Make a new one after importing more jobs, changing the contractor list, or importing a filled sheet.
