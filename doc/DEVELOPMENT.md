# Development notes (jobmatch)

Technical companion to the plain-language [README.md](../README.md). Goals and architecture: [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md). Working rules: [CLAUDE.md](../CLAUDE.md). What each change solved: [LOG-UPDATE.md](LOG-UPDATE.md).

## Status

- **Phase 1 (skeleton):** config loading (`config/*.yaml`) validated with pydantic; schemas, repository interfaces and the CSV store (atomic writes, locks, backups, validation); `validate-data`; a CPU-only Docker image with a 4 GB memory cap.
- **Phase 2 (normalization):** `jobmatch.normalize.pipeline.Normalizer` turns a `RawJob` into a `Job`:
  - EN/FR text cleanup, then splitting the posting into sections (overview, responsibilities, requirements, assets, pay, benefits, employer pitch…)
  - title, company, location, work mode, language, contract type and length, commitment (full-/part-time), skills, seniority, years of experience and pay
  - a composed `description_clean`, a fingerprint, and hard filters with `filter_reason`
- **Phase 3 (intake, manual part):** posting links (`ingest-links`), paste and saved files (`ingest-manual`), an append-only raw store / page cache in `data/raw/`, dedupe and stub completion, dead-link memory.
- **v1 (matching with Gemini, [V1_PLAN.md](V1_PLAN.md)):** anonymized contractor cards, pruned job cards, budget guard and usage log, cached judgments, and `run-once`, the whole pipeline in one command (phase 6, 2026-11-06).
- **v0 removed (2026-11-06):** the local embedding matcher (`embed/`, `match/`, the vector store, bge-m3, torch) is gone from this branch; it is kept on a separate local branch. Its findings are in [PROTOTYPES.md](PROTOTYPES.md).
- **Not built yet:** the desktop app (in progress), Adzuna/API sources, the scheduler.
- **Reminder, fix before real use:** `freshness_days` is 999 in `settings.yaml`, so the stale filter is off. There's also no "expired" filter from `JobPosting.validThrough` yet.

## Layout

```
config/            settings.yaml, sources.yaml and the YAML rules (skills, contract phrases,
                   company aliases, normalization, Canadian locations)
src/jobmatch/
  cli.py           Typer CLI (python -m jobmatch ...)
  config.py        settings and rule loaders
  schemas.py       pydantic models for every record crossing a module boundary
  ingest.py        intake orchestration: links / paste files -> normalize -> dedupe -> store
  sources/         links.py (link files), web.py (fetcher), web_page.py (page -> RawJob),
                   paste.py (paste files, URL canonicalization)
  normalize/       pipeline.py plus one module per step (sections, pay, work_mode, contract...)
  storage/         repository interfaces, CSV store, raw store
  llm/             v1 matching with Gemini: provider, payloads, prompt, judge, usage, matches, export
  pipeline.py      run-once: contractors -> jobs -> Gemini -> shortlists
  review/          placeholder (the desktop app comes next)
scripts/           fetch_and_import.sh, reset_reimport.sh, rename_manual_templates.sh
data/              runtime data (git-ignored, mounted into the container)
  inbox/links/     posting links to import, one URL per line
  inbox/manual/    hand-pasted postings (Indeed, or pages that cannot be fetched)
  raw/             raw store and page cache (append-only)
  samples/         synthetic sample data (committed)
tests/             unit and end-to-end tests; fixtures are synthetic, network is always mocked
```

## Importing jobs

1. Add posting URLs to any `.txt` file in `data/inbox/links/`, one per line. A line starting with `#` is a comment, and text after the URL and a space is ignored.
2. Run `bash scripts/fetch_and_import.sh`:
   - `--dry-run` shows the result and writes nothing
   - `--refresh` fetches every link again
   - `--links-only` skips the paste files
3. Rebuild `jobs.csv` from scratch after changing rules or the jobs schema with `bash scripts/reset_reimport.sh`:
   - `--dry-run` shows what would be moved
   - `-y` skips the question
   - `--refetch` also drops the page cache
   - It moves the generated data to `data/backups/reset-<time>/` and re-imports. Cached pages are reused, so no network is needed.

**Pages that can't be fetched** (blocked with HTTP 403, disallowed by robots.txt, or built by JavaScript like Workday): save them from the browser into `data/inbox/manual/`, and `ingest-manual` / `fetch_and_import.sh` pick them up by extension (`sources/saved_files.py`):
- **`.html` / `.htm`** ("Save page as"): read like a fetched link, `JobPosting` block first, then the page's main text. The URL comes from the canonical link, then `og:url`, then the browser's "saved from url" comment.
- **`.pdf`** ("Print > Save as PDF", headers and footers on): text extracted with `pypdf`.
  - **URL:** the one in the browser's print footer, meaning the URL found on the most pages; shortened URLs ending in "…" are ignored.
  - **Title:** the PDF's document title, else its first line.
  - **Removed:** print header and footer lines and page numbers.
  - **Not readable:** scanned or password-protected PDFs are reported as errors.
- The URL gives the same job id as the link, so a posting imported both ways is stored once. The browser's `*_files` folder and other file types are ignored.

**How fetching behaves** (config in `config/sources.yaml`):
- plain GET only: no cookies, logins or JavaScript
- 2 s between requests; robots.txt respected; timeout, size and redirect limits
- Indeed is never fetched (CLAUDE.md rule 6); LinkedIn links a person pastes are allowed
- **a page that's gone** (HTTP 404/410) is dropped. It's recorded in `data/raw/`, never fetched again (`--refresh` retries), and doesn't count as an error
- **other failures** (timeouts, 429, 5xx) are reported as errors and retried on the next run
- **page encoding:** the HTTP header's charset, else the page's `<meta charset>`, else UTF-8 with a Windows-1252 fallback

## Contractor list

`python -m jobmatch build-contractors data/contractors/contractor-master-list.xlsx` builds `data/contractors/contractors.csv`:
- It joins the Talent and Talent Profiles tabs on Talent ID, keeping only rows with `Contract? = Yes`.
- With `require_profile_text: true` (on since 2026-11-02), it also leaves out contractors with no Executive Summary and no Core Strengths; the build report counts them.
- The rules and column names are in `config/contractor_import.yaml`.
- `.xlsx` is read with the standard library (`sources/xlsx.py`), with no openpyxl or pandas.
- An existing output is copied to `data/contractors/backups/` first.
- Only counts are printed or logged, never names.

**Learned skills:** `build-contractors` also learns the contractors' core strengths that the curated `config/skills_taxonomy.yaml` doesn't recognize yet (`normalize/skill_learning.py`, rules in `config/skill_learning.yaml`):
- **Kept:** phrases of 2–5 words, or a listed acronym, that aren't stopwords.
- **Written to:** `data/contractors/skills_learned.yaml` (`paths.learned_skills`), next to the list being built, with merge hints in `skill_suggestions.csv`.
- **Loading:** `load_skills_taxonomy(config_dir, learned)` merges the file into the taxonomy, and curated terms always win.
- **Curating:** move a learned phrase into the curated file, merged with its synonyms, and it drops out of the learned file on the next build.
- **After new skills:** run `reset_reimport.sh` so jobs get the new tags. (Gemini is not sent skill tags, so this only matters for the job records.)
- **Tests:** they never read the live learned file; the conftest points `learned_skills` at a missing path.

This file is the pipeline's contractor list (`paths.contractors_csv`). `build-contractors` replaces it from the workbook; `run-once --contractors <file>` **upserts** into it instead (`sources/contractor_upsert.py`):
- accepts the `.xlsx` workbook (same rules as `build-contractors`) or a `.csv` with the list's own columns, header and rows validated;
- a new `talent_id` is added, a changed row replaces the stored one, contractors missing from the file are kept (removal is not decided);
- the list is rewritten only when something changed, after a backup in `data/contractors/backups/`; skills are not learned on upsert.

`--contractors` (and the app's **Choose folder…**) can also take a **contractor folder** (`sources/resumes.py`):
```
<folder>/
  contractors.csv          exactly one .xlsx or .csv at the top, any name
  resume/                  optional ("resume" or "resumes", any case)
    TAL-CAN-0001.pdf       one PDF per contractor, named by talent ID (case ignored)
```
- After the upsert, each PDF named by a talent ID in the list is copied to `data/contractors/resumes/<talent_id>.pdf` (atomic). An identical file is skipped; a changed one replaces the copy after the old one is moved to `resumes/old/<talent_id>-<UTC stamp>.pdf`.
- Other PDFs, and files that aren't real PDFs, are skipped. Their names are shown on screen only, never in logs or run reports (a file name can hold a person's name).
- Résumés are never sent to Gemini and don't change the profile, so a new résumé triggers no rescan.
- The browser version can't pick a folder: its upload box takes the contractor file and the PDFs together.
- The Talent card shows a **Résumé** button when a copy exists; it opens `/resume/<talent_id>` in the browser's or webview's own PDF viewer. The route serves only stored files of contractors in the list, looked up by ID.

## Matching (v1, Gemini)

Design and decisions: [V1_PLAN.md](V1_PLAN.md). Code in `src/jobmatch/llm/` and `src/jobmatch/pipeline.py`.

- **What is sent:** one call per job. The job card (`llm/payload.py`) holds the title, seniority, years, a contract line, responsibilities, requirements and nice-to-have, capped at `llm.payload.max_job_tokens`. The roster (`llm/anonymize.py`) holds every contractor as an alias (`C01`…) with headline, professions, industries, level, titles, skills, keywords and an 80-word summary, scrubbed of names, contact data and addresses. Names, location, rate, hours, proof points and talent IDs never leave the machine. `llm-preview` writes the exact requests for checking.
- **The answer** (`llm/prompt.py`): a verdict (`fit` / `partial` / `no`) with a one-line reason for every alias, and for the best `llm.top_k` a fit score, requirements met and missing, red flags and evidence quotes. Quotes are checked word for word against the text sent (`llm/evidence.py`); unverified ones are removed and flagged.
- **Budget** (`llm/usage.py`): every call is logged to `data/llm_usage.csv` with its token counts. Before a run, the planned calls are compared with what is left of today's quota (`llm.limits`, day starting at midnight Pacific); a run that would use more than `llm.budget.drain_warn_share` of it needs a yes. Calls are paced under RPM/TPM, and a daily-limit 429 stops the run cleanly.
- **Cache** (`llm/judge.py`): each answer is saved to `data/llm_results/judgments/<judge_key>.json`. The key covers the model, `PROMPT_VERSION`, the job text and the roster, so a job is asked again only when one of them changes.
- **Shortlists** (`llm/matches.py`, rule in `llm.shortlist`, user decision 2026-11-06): a job with at least one `fit` keeps fits only, the best `max_fits` (3); a job with no fit keeps its best `partials_when_no_fit` (1) partial for manual review; a job with neither keeps nothing (`no_match`). "Best" is Gemini's detailed order, then contractors it judged but didn't describe (`detailed=false`, reason only), so a self-contradicting answer ("no_fit" with fits) never hides a candidate. Rows go to `data/llm_matches.csv` (`LlmMatch`): `match_id` = judge key + talent ID, rows are only added, and a job's current shortlist is the rows of its current judge key (`CsvLlmMatchRepository.current`). Job status becomes `matched` or `no_match`. The rule filters Gemini's cached answer, so changing it needs no API call (rebuild `llm_matches.csv` to apply it to stored jobs).
- **Rounds and rescans (0.4; `shortlists.py` for I/O, `llm/rounds.py` for the pure rules):**
  - `llm_matches.csv` keeps every fit or partial candidate of each scan (`judged_job(..., everyone=True)`).
  - `job_scans.csv` records (job, contractor, `card_hash`, `basis`). A job sends only contractors without a row for its current basis and their current profile (`contractors_to_scan`), never anyone with a decision for it.
  - `job_rounds.csv` holds seats: up to `llm.shortlist` per round. A newcomer can bump an awaiting candidate. Decided candidates keep their slot. A full round opens the next one once no newcomers are left to scan.
  - Decisions are matched per (job, talent ID), so they survive rescans.
  - `applications.csv` holds approved candidates' application statuses: `rejected` removes them for that job, `hired` completes the job.
  - `sync_rounds` runs on every screen load and at the end of a run. First it calls `absorb_cached`, which records saved judgments that aren't recorded yet. That's how 0.3 data is taken over with no API call.
- **`run-once`** (`pipeline.py`): optional contractor upsert → ingest links and pastes → budget check (asks through a `confirm` callback) → judge → store shortlists and statuses → one row in `runs.csv`. It holds `data/.pipeline.lock`, so runs never overlap, and is safe to re-run.
- **`export-llm-matches`** (`llm/export.py`): the current shortlists for Excel, with names; jobs without matches or not judged yet get one row saying so. Formula-like cells are neutralized.
- **`llm-compare`** (`llm/compare.py`): Gemini's cached verdicts against the blind labels in `data/eval/blind_consensus.csv` (agreement, kappa, what is surfaced, within-job ranking). No API call.

## Running (Docker)

Every command runs inside the app container. CLAUDE.md rule 2 requires approval before running any of them.

```
docker compose build
docker compose run --rm app python -m jobmatch ingest-links data/inbox/links [--dry-run] [--refresh]
docker compose run --rm app python -m jobmatch ingest-manual data/inbox/manual [--dry-run]
docker compose run --rm app python -m jobmatch build-contractors data/contractors/contractor-master-list.xlsx
docker compose run --rm app python -m jobmatch validate-data
docker compose run --rm app python -m jobmatch validate-data --data-dir data/samples
docker compose run --rm app python -m jobmatch llm-judge --dry-run       # calls needed, quota check; sends nothing
docker compose run --rm app python -m jobmatch run-once [--contractors <folder or file>] [--yes] [--max-calls N]
docker compose run --rm app python -m jobmatch export-llm-matches
docker compose run --rm app python -m jobmatch llm-compare
docker compose run --rm app pytest
docker compose run --rm app ruff check .
```

Dependencies are pinned in `uv.lock`, resolved for linux/amd64 and limited to releases at least about 2 weeks old (`exclude-newer` in `pyproject.toml`). There is no ML library or model download any more. After changing dependencies, re-lock and run `docker compose build`.

## Desktop app (in progress)

Option A: a native app with no Docker (NiceGUI in a pywebview window, packaged with PyInstaller as an Intel Mac `.app` and a Windows `.exe`).
- **Name and theme:** the app is called **do good talent** (window, header, Mac Finder/Dock: `app_paths.APP_TITLE`); its files are `dogoodtalent.app` / `dogoodtalent.exe` and its data folder `dogoodtalent` (`APP_FOLDER`). The code package and CLI stay `jobmatch`. Theme: light purple (`THEME_PRIMARY` `#7E57C2` for the header and buttons, `THEME_BACKGROUND` `#EDE7F6` for the page, in `app/ui.py`); the green/yellow/red decision colours are separate.
- **Dependencies:** the `app` extra (`nicegui`, `pywebview`) and the `build` group (`pyinstaller`). Docker installs neither. `uv.lock` covers linux x86_64, macOS x86_64 and Windows AMD64; every compiled package has a wheel for each platform that needs it (checked 2026-11-06).
- **Folders** (`app_paths.py`): user data root `~/Library/Application Support/dogoodtalent` (Mac) or `%APPDATA%\dogoodtalent` (Windows), holding `data/`, `exports/` and `.env`; config is bundled read-only (`sys._MEIPASS/config`). The app calls `load_settings(bundled_config_dir(), data_root=user_data_root())`.
- **Secrets** (`secrets_file.py`): the app loads its `.env` into the environment at startup and saves the Gemini key from its Settings screen (atomic write, owner-only file, never logged).
- **Screens** (`app/ui.py`, NiceGUI 3 `ui.sub_pages`): `/` find matches, `/results`, `/jobs` (every job A–Z by state, with Completed / Unavailable / Reopen, and a caret per job with its approved candidates' application status), `/talent` (every contractor, the "Available for matching" toggle stored in `talent_availability.csv`, the jobs each was approved for; `build_roster(..., available=)` applies the toggle, and `reset_toggles_on_import` lets a new or changed imported profile take precedence), `/settings`. All logic is in `app/service.py` (`AppService`), tested without a UI; the screens are tested with NiceGUI's simulated user (`user_simulation`, run with `asyncio.run`, no pytest-asyncio). The run goes to a worker thread (`run.io_bound`); progress reaches the screen through a queue, and the quota question through a dialog awaited on the UI loop.
- **Start:** `python -m jobmatch.app` (native window, user data root) or `python -m jobmatch.app --browser` (repo data). Preview in Docker: `docker compose run --rm --service-ports app python -m jobmatch.app --browser --host 0.0.0.0`, then http://localhost:8080 (published on 127.0.0.1 only).

### Packaging (G3)

- **Recipe:** `packaging/jobmatch.spec` (PyInstaller), with `packaging/launch.py` as the entry script (it calls `multiprocessing.freeze_support()` first: NiceGUI's native window runs in a child process). It bundles the code, NiceGUI's and pywebview's files and `config/`; never `data/`, `exports/` or `.env`. No UPX.
- **Outputs:** an Intel Mac `dogoodtalent.app` (x86_64, minimum macOS 10.15, bundle ID `org.dogoodtalent.desktop`) in `dogoodtalent-mac-intel.dmg` with an Applications shortcut; a Windows folder `dogoodtalent/` with `dogoodtalent.exe`, zipped as `dogoodtalent-windows.zip`. Both unsigned (first-launch steps in [FIRST_LAUNCH.md](FIRST_LAUNCH.md)).
- **Builds:** `.github/workflows/build-app.yml` on GitHub Actions (`macos-15-intel`, GitHub's last Intel runner, supported until fall 2027; `windows-latest`). Run it from Actions › build-app › Run workflow (a test build), or push a `v*` tag (a release, below). Each build runs the tests, builds, runs the app with `--check` and uploads the download (kept 14 days). No secrets.
- **Version:** one number, `version` in `pyproject.toml`. The spec reads it for the Mac app's version (Finder › Get Info).

### CI and releases

- **CI** (`.github/workflows/ci.yml`): every push and pull request, on Linux: `ruff check`, `ruff format --check`, `pytest`. About 2–3 minutes. A red ✗ on a commit in GitHub means one of them failed; open it for the log.
- **Release** (tag-triggered `build-app.yml`):
  1. Raise `version` in `pyproject.toml` (for example `0.2.0`), and add a LOG-UPDATE entry.
  2. Commit, push, then tag that commit `v0.2.0` and push the tag (`git tag v0.2.0`, `git push origin v0.2.0`).
  3. GitHub checks the tag matches the version, builds and checks both apps, then creates a **draft** release with `dogoodtalent-mac-intel.dmg` and `dogoodtalent-windows.zip`.
  4. Open GitHub › Releases, look over the draft, click **Publish release**. Only then can others see it.
  - A wrong tag (not `v` + the version) stops in the first minute and publishes nothing. A failed build or check also publishes nothing.
  - Once published, the newest files are always at `https://github.com/<owner>/<repo>/releases/latest` (fixed file names, so `…/releases/latest/download/dogoodtalent-mac-intel.dmg` always gives the newest Mac build).
  - To redo a version: delete the draft release and the tag on GitHub, fix, and tag again.
- **`--check`:** `python -m jobmatch.app --check [--data-root DIR]` loads the bundled config, NiceGUI's web files and every module the app needs (`CHECK_MODULES`), writes `<data root>/check.txt` and exits 0 or 1, without a window.

## Storage conventions

**Storage backend (DataLoader factory, `storage/loader.py`).** Code outside `storage/` never builds a store itself: it calls `create_loader(settings)` and uses the repositories it returns (`jobs`, `contractors`, `llm_matches`, `reviews`, `runs`, `llm_usage`; interfaces in `storage/repositories.py`).
- `storage.backend` in `settings.yaml` picks the backend: `csv` (default, the files below) or `mongodb`.
- `mongodb` is a placeholder (`MongoDataLoader`) until hosting is decided. Creating it fails with a plain message, and `run-once`, the app and its `--check` all report it. No MongoDB library is installed. Connection details will come from `.env`, never from settings.
- Not behind the loader, on purpose: the raw page cache (`data/raw/`), Gemini's cached answers (`data/llm_results/`) and `llm-preview` folders. They're caches and working files.
- `validate-data` checks the CSV files directly (a CSV tool by design). A test (`test_only_storage_builds_csv_stores`) fails if anything else outside `storage/` starts building CSV stores.


- `data/contractors/contractors.csv` is built from the human-maintained workbook (`build-contractors`) or upserted from a contractor file (`run-once --contractors`); both back up the old file first. The workbook itself is never written.
- `data/contractors/resumes/` holds the résumé copies (`<talent_id>.pdf`), with replaced ones kept in `old/`. Personal data: never sent anywhere.
- `reviews.csv`, `runs.csv`, `llm_usage.csv`, `llm_matches.csv`, `job_scans.csv`, `job_rounds.csv`, `applications.csv`, `talent_availability.csv` and `contractor_changes.csv` are append-only. Rows leave them only through a deletion the reviewer confirmed (Delete job / Delete talent: `CsvTable.purge`, backup first). `removed_jobs.csv` keeps deleted jobs on the Talent screen. Deleting a talent also deletes the decisions on them: a deliberate exception to "every decision is kept" (user decisions 2026-11-10). `contractor_changes.csv` drives rescans: see `shortlists.py` (only contractors never judged for a job, or changed since, are sent). Every rewrite of another CSV is preceded by a timestamped copy in `data/backups/`.
- Each CSV jobmatch writes gets a `<name>.schema.json` sidecar recording its `schema_version` (jobs: version 3). After a schema change, rebuild with `scripts/reset_reimport.sh`.
- `data/raw/<source>-YYYY-MM.jsonl` keeps every posting as received: paste files, and the extracted `JobPosting` data or main page HTML for links. Identical records are written once.
- Multi-value cells use `|`. A literal `|` or `\` inside an item is escaped with a backslash.
- v0 left `data/matches.csv`, `data/matches_flagged.csv` and `data/embeddings/` behind. Nothing reads them any more; deleting them is a separate decision.
