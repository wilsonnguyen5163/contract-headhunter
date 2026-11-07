# Quick start

How to import jobs, load contractors, run matching with Gemini and read the results. For background, see [README.md](../README.md). Technical details are in [DEVELOPMENT.md](DEVELOPMENT.md).

Every `jobmatch` command runs inside the Docker container:

```
docker compose run --rm app python -m jobmatch <command>
```

The shell scripts in `scripts/` call the container for you. Run them from the repo root. (A desktop app for end users is planned; this guide is for running it from the repo.)

---

## 1. Important commands

### The whole run

| Command | What it does |
|---|---|
| `python -m jobmatch run-once` | **The main command.** Imports new jobs from `data/inbox/`, asks Gemini to judge every active job not judged yet (one call each), and stores the shortlists in `data/llm_matches.csv`. Cached answers cost nothing, so re-running is free. |
| `python -m jobmatch run-once --contractors <file>` | Same, after loading a contractor file (`.xlsx` master workbook or `.csv`): new talent IDs are added, changed ones updated, others kept. |
| `python -m jobmatch run-once --yes` | Goes ahead when the run would use most of today's Gemini quota (or, if it can't finish today, sends what fits). Without it, the run stops and asks. |
| `python -m jobmatch run-once --max-calls N` | Sends at most N calls. |
| `python -m jobmatch export-llm-matches` | Writes the current shortlists, with contractor names, to `exports/llm-matches-<time>.csv` for Excel. |

### Importing jobs only

| Command | What it does |
|---|---|
| `bash scripts/fetch_and_import.sh` | Fetches every new link in `data/inbox/links/*.txt`, imports the paste and saved files in `data/inbox/manual/`, and stores them in `data/jobs.csv`. Links fetched before are read from the cache in `data/raw/`, with no network. `run-once` does this too. |
| `bash scripts/fetch_and_import.sh --dry-run` | Shows what would be imported and writes nothing. |
| `bash scripts/fetch_and_import.sh --refresh` | Fetches every link again, ignoring the cache. |
| `bash scripts/reset_reimport.sh` | Rebuilds `jobs.csv` from scratch. It asks first, `-y` skips the question, `--dry-run` only shows what would happen, and `--refetch` also drops the page cache. |
| `bash scripts/rename_manual_templates.sh [--dry-run]` | Renames filled-in `_TEMPLATE copy*.txt` files to their job title so they get imported. |

### Contractors

| Command | What it does |
|---|---|
| `python -m jobmatch build-contractors data/contractors/contractor-master-list.xlsx` | Rebuilds `data/contractors/contractors.csv` from the master workbook (replaces it, after a backup). `run-once --contractors` updates it instead. |
| `python -m jobmatch suggest-professions` / `apply-professions <file>` | One Gemini call suggests a profession for every contractor; a person checks the file, then applies it. |

### Gemini tools and checks

| Command | What it does |
|---|---|
| `python -m jobmatch llm-judge --dry-run` | How many Gemini calls the active jobs still need, and whether today's quota allows them. Sends nothing. |
| `python -m jobmatch llm-preview` | Writes the exact requests that would be sent to `data/llm_preview/<time>/`, so you can check what leaves the machine. Sends nothing. |
| `python -m jobmatch llm-try --job-id <id>` | One real call for one job, printed in full. |
| `python -m jobmatch llm-compare` | Compares Gemini's cached verdicts with the blind labels in `data/eval/`. No API call. |
| `python -m jobmatch validate-data` | Checks config and every CSV against their schemas. |
| `pytest` / `ruff check .` | Tests and lint. |

---

## 2. Quick start guide

### Step 0: one-time setup

```
cp .env.example .env      # then put your Gemini key after GEMINI_API_KEY=
docker compose build
```

The key comes from Google AI Studio (free tier). Never put it anywhere but `.env`.

### Step 1: add jobs

**A. Paste a link** (most job boards, company career pages, LinkedIn job links):
1. Open or create any `.txt` file in `data/inbox/links/`, for example `2026-11-06.txt`.
2. Add one URL per line. Lines starting with `#` are comments.

**B. Paste the posting by hand.** Use this for pages the tool can't read: login walls, or pages that need JavaScript such as Workday, Zoho Recruit and BambooHR.
- **Text:** copy `data/inbox/manual/_TEMPLATE.txt` and fill in `url:` and `title:` (other fields are optional). Paste the posting text below the `---` line and save it as UTF-8. Files whose names start with `_` are never imported, so either rename the copy or run `bash scripts/rename_manual_templates.sh`.
- **Saved page:** in the browser, use "Save page as" to save a `.html` file into `data/inbox/manual/`.
- **PDF:** "Print > Save as PDF" with headers and footers on, saved into `data/inbox/manual/`.

### Step 2: run

```
docker compose run --rm app python -m jobmatch llm-judge --dry-run    # optional: how many calls
docker compose run --rm app python -m jobmatch run-once               # add --contractors <file> when the list changed
docker compose run --rm app python -m jobmatch export-llm-matches
```

`run-once` prints one line per Gemini call with its token use and today's total, then a summary: jobs with matches, jobs with none, jobs not judged yet (for example when the quota ran out; the next run picks them up).

### Step 3: read the results

Open `exports/llm-matches-<time>.csv` in Excel. What each job shows (rule in `llm.shortlist` in `config/settings.yaml`):
- **Jobs with clear fits:** the fits only, up to 3, best first.
- **Jobs with no clear fit:** the single best partial fit, for you to check by hand.
- **Jobs with neither:** one `no match` row.

Gemini still judges every contractor; the others stay in its cached answer, so changing the rule needs no new call. One row per suggested contractor:

| Column | Meaning |
|---|---|
| `result` | `match`, `no match` (Gemini judged nobody fit), or `not judged yet`. |
| `rank` | 1 = best for this job. |
| `verdict` | `fit` (could do the core of the job now) or `partial` (part of it, or a clear gap). |
| `detail` | `full`, or `reason only` when Gemini judged the contractor without describing them (rare). **Check these by hand.** |
| `fit_score` | 0 to 100, Gemini's ordering number (empty for `reason only`). |
| `reason` | Gemini's one-line reason. |
| `requirements_met`, `requirements_missing`, `red_flags` | From the posting, as Gemini read it. |
| `evidence` | Quotes from the posting and profile, each checked word for word against the text sent. `evidence_check` says if one was removed. |

**The export contains contractor names.** Keep it on your machine and don't share it outside the team. The tool only recommends: a person makes every decision.

### When needed: reset and re-import

Rebuild `jobs.csv` after you change the rules in `config/` (normalization, contract phrases) or after the jobs schema changes:

```
bash scripts/reset_reimport.sh --dry-run   # shows what would be moved
bash scripts/reset_reimport.sh -y
```

It moves `jobs.csv` and `runs.csv` into `data/backups/reset-<time>/` (nothing is deleted) and re-imports everything in `data/inbox/` from the page cache. Jobs whose text is unchanged keep their cached Gemini answers.

### Where things are stored

| File | What's in it |
|---|---|
| `data/jobs.csv` | Every imported job, normalized, with its status (`new`, `matched`, `no_match`, `filtered_out`, `needs_details`). |
| `data/llm_matches.csv` | Each job's shortlist (up to 3 fits, or the best partial), per judgment. Rows are only added. |
| `data/llm_results/judgments/` | Gemini's answers, cached. A job is asked again only when its text, the contractor list, the model or the prompt changes. |
| `data/llm_usage.csv` | One row per Gemini call: tokens and status, never text. Read by the quota check. |
| `data/contractors/contractors.csv` | The contractor list (personal data: stays local). |
| `exports/llm-matches-<time>.csv` | The spreadsheet for people. |
