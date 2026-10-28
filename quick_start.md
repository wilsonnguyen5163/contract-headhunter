# Quick start

How to import jobs, run the matcher and read the results. For background, see [README.md](README.md). Technical details are in [DEVELOPMENT.md](DEVELOPMENT.md).

Every `jobmatch` command runs inside the Docker container:

```
docker compose run --rm app python -m jobmatch <command>
```

The shell scripts in `scripts/` call the container for you. Run them from the repo root.

---

## 1. Important commands

### Importing jobs

| Command | What it does |
|---|---|
| `bash scripts/fetch_and_import.sh` | Fetches every new link in `data/inbox/links/*.txt`, imports the paste and saved files in `data/inbox/manual/`, and stores them in `data/jobs.csv`. Links fetched before are read from the cache in `data/raw/`, with no network. |
| `bash scripts/fetch_and_import.sh --dry-run` | Shows what would be imported and writes nothing. |
| `bash scripts/fetch_and_import.sh --refresh` | Fetches every link again, ignoring the cache. |
| `bash scripts/fetch_and_import.sh --links-only` | Imports the links only and skips `data/inbox/manual/`. |
| `bash scripts/reset_reimport.sh` | Rebuilds `jobs.csv` from scratch. It asks first, `-y` skips the question, `--dry-run` only shows what would happen, and `--refetch` also drops the page cache. |
| `bash scripts/rename_manual_templates.sh [--dry-run]` | Renames filled-in `_TEMPLATE copy*.txt` files to their job title so they get imported. |
| `python -m jobmatch ingest-links data/inbox/links [--dry-run] [--refresh]` | The links half of `fetch_and_import.sh`. |
| `python -m jobmatch ingest-manual data/inbox/manual [--dry-run]` | The paste-file half: `.txt`, `.html` and `.pdf`. |

### Contractors

| Command | What it does |
|---|---|
| `python -m jobmatch build-contractors data/contractors/contractors_master_list.xlsx` | Builds `data/contractors/contractors.csv` from the master workbook. It keeps confirmed contract talent only and backs up the old file first. It also learns new skills into `skills_learned.yaml`. |

### Matching

| Command | What it does |
|---|---|
| `python -m jobmatch embed-contractors` | Turns each contractor profile and title into vectors. Only changed profiles are redone. Run it after the contractor list changes. |
| `python -m jobmatch embed-jobs` | Turns each active job and its title into vectors. Only new or changed jobs are redone. |
| `python -m jobmatch match --dry-run` | Scores every job against every contractor and prints the shortlists. It stores nothing. |
| `python -m jobmatch match` | Same as the dry run, but stores the results in `data/matches.csv` and `data/matches_flagged.csv`. |
| `python -m jobmatch export-trial-run` | Writes the best 2 contractors per job to `exports/trial-run-<time>.csv`. **This is the file the notebook reads.** |
| `python -m jobmatch export-matches` | Writes every current match (shortlist and flagged) with all scores to `exports/matches-<time>.csv`. |

### Evaluation and checks

| Command | What it does |
|---|---|
| `python -m jobmatch import-reviewed-matches <sheet.csv>` | Imports a human-reviewed match sheet into `data/eval/reviewed_matches.csv`. |
| `python -m jobmatch evaluate-matches` | Compares the matcher's scores with the human reviews and writes `exports/evaluation-<time>.csv`. Read-only. |
| `python -m jobmatch validate-data` | Checks config, every CSV and the vector files against their schemas. |
| `python -m jobmatch smoke-embed` | Loads the embedding model and encodes a few test texts. |
| `pytest` / `ruff check .` | Tests and lint. |

---

## 2. Quick start guide

### Step 0: one-time setup

```
cp .env.example .env
docker compose build
# Downloads the embedding model once (BAAI/bge-m3, about 4.5 GB) into the hf-cache Docker volume:
docker compose run --rm -e HF_HUB_OFFLINE=0 -e TRANSFORMERS_OFFLINE=0 app python -m jobmatch smoke-embed
```

After the download, keep `HF_HUB_OFFLINE=1` in `.env` so the container never contacts Hugging Face again.

If the contractor list isn't built yet:

```
docker compose run --rm app python -m jobmatch build-contractors data/contractors/contractors_master_list.xlsx
```

### Step 1: import jobs

There are two ways to add a job.

**A. Paste a link** (most job boards, company career pages, LinkedIn job links):
1. Open or create any `.txt` file in `data/inbox/links/`, for example `2026-10-28.txt`.
2. Add one URL per line. Lines starting with `#` are comments.
3. Indeed links are always skipped. Use option B for those.

**B. Paste the posting by hand.** Use this for Indeed, and for pages the tool can't read: login walls, or pages that need JavaScript such as Workday, Zoho Recruit and BambooHR.
- **Text:** copy `data/inbox/manual/_TEMPLATE.txt` and fill in `url:` and `title:` (other fields are optional). Paste the posting text below the `---` line and save it as UTF-8. Files whose names start with `_` are never imported, so either rename the copy or run `bash scripts/rename_manual_templates.sh`.
- **Saved page:** in the browser, use "Save page as" to save a `.html` file into `data/inbox/manual/`.
- **PDF:** "Print > Save as PDF" with headers and footers on, saved into `data/inbox/manual/`.

Then import:

```
bash scripts/fetch_and_import.sh --dry-run   # optional: look first
bash scripts/fetch_and_import.sh
```

The summary shows each posting as new, duplicate, filtered out (not Canadian or not a contract), needs details, or error. Re-running is safe: jobs that are already stored are skipped.

### Step 2 (when needed): reset and re-import

Rebuild `jobs.csv` after you change the rules in `config/` (skills, normalization, contract phrases), after `build-contractors` learns new skills, or after the jobs schema changes:

```
bash scripts/reset_reimport.sh --dry-run   # shows what would be moved
bash scripts/reset_reimport.sh -y
```

- It moves `jobs.csv` and `runs.csv` into `data/backups/reset-<time>/`. Nothing is deleted.
- It then re-imports everything in `data/inbox/`. Pages already fetched come from the cache, so no network is needed. Add `--refetch` to fetch them again.
- It never touches `data/inbox/`, `data/samples/` or the contractor files.
- Don't run it while another import is running.

After a reset, run all of step 3 again, including `embed-contractors`.

### Step 3: run the model

```
docker compose run --rm app python -m jobmatch embed-contractors   # only after the contractor list or skills change
docker compose run --rm app python -m jobmatch embed-jobs
docker compose run --rm app python -m jobmatch match --dry-run     # optional: look first
docker compose run --rm app python -m jobmatch match
docker compose run --rm app python -m jobmatch export-trial-run
```

`match` prints each job's shortlist with talent IDs (never names), the score, the band and any missing must-have skills.

How a match is scored (weights are in `config/settings.yaml`, all are first guesses):
- **Overall meaning:** the whole job text against the whole profile.
- **Title:** the job title against the contractor's closest title.
- **Skills:** the share of the job's skills the contractor lists.
- **Type of work / industry:** the job's field against the contractor's.
- **Bands:** high from 0.70, medium from 0.50, low below that. Scores below `min_score` (0.40) aren't shortlisted.
- **Flagged:** a match that fails a rule check, such as a seniority gap of two or more levels, goes to the flagged list with the reason.

### Step 4: where the results are stored

| File | What's in it |
|---|---|
| `data/jobs.csv` | Every imported job, normalized, with its status (`new`, `matched`, `no_match`, `filtered_out`, `needs_details`). |
| `data/matches.csv` | Shortlisted matches that passed every check. Append-only: a re-run with the same settings adds nothing, and changed settings add a new set of rows. |
| `data/matches_flagged.csv` | Matches that failed a check, with the reason in `gates_failed`. |
| `data/embeddings/*.npz` | Vectors for jobs and contractors. Not human-readable. |
| `exports/trial-run-<time>.csv` | The best 2 contractors per job, for the notebook. |
| `exports/matches-<time>.csv` | Every current match with all scores and contractor details, for Excel. |
| `exports/evaluation-<time>.csv` | Matcher compared with the human reviews. |

**The exports contain contractor names.** Keep them on your machine and don't share them outside the team.

### Step 5: read the results in `data_visualizer.ipynb`

The notebook runs on your own machine, not in the container. It needs Jupyter (or VS Code with the Jupyter extension) and `pandas`. Its file paths are absolute (`/home/wilson/Work/do_good/contract-headhunter/...`), so change them to your copy of the repo first.

1. Run `export-trial-run` (step 3) so there is a fresh `exports/trial-run-*.csv`.
2. Open `data_visualizer.ipynb` and run all cells.
3. `view_trial_run()` shows the newest trial run. It prints one table with up to 2 contractors per job:

| Column | Meaning |
|---|---|
| `job_title`, `company` | The job, shown once per job. |
| `option` | 1 = best contractor for this job, 2 = second best. |
| `score` | 0 to 1; higher is a closer fit. |
| `band` | `high` (0.70 and above), `medium` (0.50 to 0.70), `low` (0.40 to 0.50), or `no match` (no contractor reached 0.40). |
| `talent_name`, `talent_title` | The contractor and their headline. |
| `shared_skills` | Skills found in both the job and the profile, joined with `\|`. |

Filters:

```python
view_trial_run(band='high')                # strong fits only
view_trial_run(band=['high', 'medium'])    # strong and possible fits
view_trial_run(band='no match')            # jobs nobody in our pool fits
view_trial_run('exports/trial-run-20261028T014250Z.csv')   # an older run
```

4. `view_talent_details('TAL-BC-001')` shows a contractor's profile: location, summary, industry, strengths, proof points and target titles. Use it to check why someone was suggested. A thin profile (no summary or strengths) often means good fits get missed.

**How to read it:**
- Treat a high band as a good first guess, medium as "check it", and low as a weak fit.
- Check `shared_skills` against the job: an empty or very generic list (such as "go-to-market" alone) means the match rests on wording, not skills.
- For details the notebook doesn't show (missing must-have skills, flags, why a match was flagged), open `exports/matches-<time>.csv` from `export-matches` in Excel.
- The tool only recommends. A person makes every decision.
