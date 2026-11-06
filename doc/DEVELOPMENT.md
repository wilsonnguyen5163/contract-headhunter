# Development notes (jobmatch)

Technical companion to the plain-language [README.md](../README.md). Goals and architecture: [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md). Working rules: [CLAUDE.md](../CLAUDE.md). What each change solved: [LOG-UPDATE.md](LOG-UPDATE.md).

## Status

- **Phase 1 (skeleton):**
  - config loading (`config/*.yaml`), validated with pydantic
  - schemas, repository interfaces, the CSV store (atomic writes, locks, backups, validation) and the NumPy vector store
  - `validate-data` and `smoke-embed`
  - CPU-only Docker image with a 4 GB memory cap
- **Phase 2 (normalization):** `jobmatch.normalize.pipeline.Normalizer` turns a `RawJob` into a `Job`. That covers:
  - EN/FR text cleanup, then splitting the posting into sections (overview, responsibilities, requirements, assets, pay, benefits, employer pitch…)
  - title, company, location, work mode, language, contract type and length, commitment (full-/part-time), skills, seniority, years of experience and pay
  - a composed `description_clean` (name → responsibilities → requirements → pay), a fingerprint, and hard filters with `filter_reason`
- **Phase 3 (intake, manual part):**
  - **posting links** (`ingest-links`): fetch each link a person adds and read its schema.org `JobPosting` block, falling back to the page's main text
  - **paste files** (`ingest-manual`) as the fallback
  - an append-only raw store / page cache in `data/raw/`
  - dedupe: same source and ID; same fingerprint; or a similar title (rapidfuzz, after expanding "Sr."/"Mgr"…) at the same company, city and seniority; stub completion
  - dead links (HTTP 404/410) are dropped and remembered; empty input files are skipped
- **Phase 4 (matching):**
  - contractor list built from the master workbook (`build-contractors`)
  - job and contractor vectors, full texts and titles (`embed-jobs`, `embed-contractors`), with `BAAI/bge-m3`
  - the matcher (`match`) and a spreadsheet export (`export-matches`)
- **Not built yet:** Adzuna/API sources, the evaluation script (waits for reviewer data), review UI (phase 5), the scheduler (phase 6).
- **Reminder, fix before real use:** `freshness_days` is 999 in `settings.yaml`, so the stale filter is off while matching is tested. There's also no "expired" filter from `JobPosting.validThrough` yet.

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
  storage/         repository interfaces, CSV store, raw store, NumPy vector store
  embed/           local sentence-transformers encoder, smoke check, embedding texts
                   (texts.py) and incremental vector building (build.py)
  match/ review/ llm/   placeholders for later phases (llm/ stays empty in v0)
scripts/           fetch_and_import.sh, reset_reimport.sh
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
- With `require_profile_text: true` (on since 2026-11-02), it also leaves out contractors with no Executive Summary and no Core Strengths; the build report counts them. `embed-contractors` then drops their vectors.
- The rules and column names are in `config/contractor_import.yaml`.
- `.xlsx` is read with the standard library (`sources/xlsx.py`), with no openpyxl or pandas.
- An existing output is copied to `data/contractors/backups/` first.
- Only counts are printed or logged, never names.

**Learned skills:** `build-contractors` also learns the contractors' core strengths that the curated `config/skills_taxonomy.yaml` doesn't recognize yet (`normalize/skill_learning.py`, rules in `config/skill_learning.yaml`):
- **Kept:** phrases of 2–5 words, or a listed acronym, that aren't stopwords.
- **Written to:** `data/contractors/skills_learned.yaml` (`paths.learned_skills`), next to the list being built, with merge hints in `skill_suggestions.csv`.
- **Loading:** `load_skills_taxonomy(config_dir, learned)` merges the file into the taxonomy, and curated terms always win.
- **Curating:** move a learned phrase into the curated file, merged with its synonyms, and it drops out of the learned file on the next build.
- **After new skills:** run `reset_reimport.sh`, `embed-contractors`, `embed-jobs` and `match`, so jobs get the new tags.
- **Tests:** they never read the live learned file; the conftest points `learned_skills` at a missing path.

This file is the pipeline's contractor list: `embed-contractors` and `match` read it (`paths.contractors_csv`). The earlier `Contractor`/facet schema was removed after the facet prototype.

## Embeddings

`embed-contractors` and `embed-jobs` turn each contractor and each active job (`new`, `matched`, `no_match`) into one 1024-dimension vector with `BAAI/bge-m3` (pinned revision in `settings.yaml`, CPU only, no prefixes, normalized, cut at 512 tokens).

The text embedded for each row is built in `embed/texts.py`. Both sides use the same labels in the same order, most useful first, because the tail is cut:

| Label | Contractor | Job |
|---|---|---|
| `Title` | `standardized_headline` | `title_norm` |
| `Target titles` | `target_titles` | — |
| `Seniority` | `seniority` | `seniority` |
| `Skills` | taxonomy skills found in the profile (keywords included), then remaining `core_strengths` | `skills` |
| `Keywords` | `search_keywords` (the workbook's Search Keywords) not already listed | — |
| `Industries` | `main_industry` | — |
| `Responsibilities` / `Requirements` / `Nice to have` | — | section lists (else `Description` from `description_clean`) |
| `Summary` / `Proof points` | `executive_summary`, `proof_points` | — |

Names, IDs, location, pay or rate, hours, work mode, contract type and company are left out: they are gates in matching, not meaning, and names are personal data.

Re-runs are incremental (`embed/build.py`): a vector is reused when the row's text hash, model, model version and template are unchanged; rows that are gone are dropped. Editing a template means bumping `TEMPLATE_VERSION` in `texts.py`, which re-embeds everything. The exact texts are written to `data/embeddings/<namespace>_texts.jsonl` for checking.

**Titles are embedded separately** (`job_titles`, `contractor_titles`, `TITLE_TEMPLATE_VERSION`):
- the job title, and each contractor title (headline plus target titles, ids like `TAL-ON-028#1`)
- junior, intermediate and senior words removed (seniority has its own check); bracketed text removed from job titles
- lead and executive words kept, since they name the role
- cleaned titles are lowercase and accent-free

**Profession vectors** (`job_cores`, `professions`, `PROFESSION_TEMPLATE_VERSION`, built by `embed-jobs` when `config/professions.yaml` exists):
- each job's core: the title and its first 3 responsibilities, falling back to requirements, then to the description's first sentences (`job_core_text`);
- each profession's name and description.

## Profession notes

`match/professions.py` writes a hint for the reviewer, for example `job: recruiting and talent acquisition · talent: sales and business development (different)`. **It never changes a score or a shortlist.**
- **The job's profession** is the description in `config/professions.yaml` closest to its core vector. The 68 professions cover our contractors' fields plus the other NOC 2021 broad categories (health, education, trades, public safety…), so a job outside our fields gets an honest profession rather than the nearest marketing one.
- **The contractor's professions** come from their title vectors (headline and target titles).
- **Clear assignments only:** a profession counts only when it beats the second-best by `min_margin`, so generic titles ("Consultant") are skipped.
- **Verdicts:**
  - `same`;
  - `close`: a pair under `close`;
  - `different`;
  - `unclear`: the job's profession isn't clear, or none of the contractor's titles is.
- **Where the note appears:** the `match` output, and a `profession` column in `export-trial-run` and `export-matches`. It isn't stored in `matches.csv`.
- **Missing or stale vectors:** if one vector set is missing or out of date, the note is left out and the command says which embed step to run.
- **On the 155 blind labels** (2026-11-04): "different" covers 2 fits and 53 non-fits, and 10 of 82 jobs are unclear. As a score penalty it isn't proven: see [PROTOTYPES.md](PROTOTYPES.md).

## Matching

`match [--dry-run]` scores every active job against every contractor from the stored vectors. No model is loaded. Code: `match/scorer.py`, `match/gates.py`, `match/run.py`.

**Score:** a weighted average of five parts, each 0–1. A part with nothing to compare is left out, and the others share its weight.

| Part | What it measures | Settings |
|---|---|---|
| text | full job text vs full profile, cosine stretched between floor and ceiling | `cosine_floor`, `cosine_ceiling`, `weights.cosine` |
| title | job title vs the contractor's closest title | `title_floor`, `title_ceiling`, `weights.title` |
| skills | shared taxonomy skills ÷ min(job's skill count, cap) | `skill_overlap_cap`, `weights.skill_overlap` |
| field | share of the job's type of work (its skill tags' categories, counted) that the contractor's skill categories cover; `management`, `business` and learned skills are ignored | `weights.field`, `field_ignore_categories` |
| words | TF-IDF word overlap of the raw posting (title + `description_raw`) and the whole profile (search keywords included), accent-insensitive, no skill list (`match/lexical.py`). Word rarity is counted over the active jobs and contractors, so its fingerprint is part of the scoring version | `weights.lexical`, `lexical_floor`, `lexical_ceiling`, `lexical_stopwords` |

The skill list is **frozen for ranking** since 2026-11-02 (see the note at the top of `config/skills_taxonomy.yaml` and [PROTOTYPES.md](PROTOTYPES.md)). TF-IDF covers vocabulary without aliases; the skill list remains for the reviewer-facing explanation (matched and missing skills) and the type-of-work categories. The `words` value appears in `match` output but isn't stored in `matches.csv` (that would need a schema migration of an append-only file).

- **Thin profiles:** a profile with no summary and no core strengths is scored on title and skills only (`thin_profiles_skip_text`).
- **Bands:** high / medium / low come from the score (`bands`).
- **Must-have skills:** skills in Requirements lines, except lines with soft wording (`must_have_soft_markers` in `normalization.yaml`). They're always shown, and lower the band only if `missing_must_have_lowers_band` is on (off since 2026-10-26).

**Rule checks** (`gates.py`). Unknown data always passes:
- **Seniority:** fails when the job is more than `seniority_max_gap` levels from every level the contractor holds, in either direction. The reason names both sides, for example `seniority: under-qualified (job senior, contractor junior)`.
- **Location:** off while `assume_all_remote` is true (user decision, 2026-10-26). When on, an on-site or hybrid job in another province fails.

**Information only, never a filter:**
- rate above posted pay (yearly pay ÷ `hours_per_year`, with `rate_tolerance`)
- `field: job marketing · contractor sales`, when the job's main type of work isn't among the contractor's (`match/fields.py`)
- `same industry: …` or `industry: job … · contractor …`. The job's industry is found in its title, company and cleaned description (at most 2); the contractor's comes from `main_industry`. Both use `config/industries.yaml`; generic words are allowed only in contractor lists (`contractor_aliases`). Industry is never scored
- posting in French or bilingual
- thin profile

Hours and full-/part-time are shown, never checked.

**Shortlist and storage:**
- **What's kept:** up to `top_k` contractors that pass every check and score at least `min_score`, plus any flagged contractor ranked above the last of them.
- **Where:** passing matches go to `data/matches.csv`, flagged ones (a check failed) to `data/matches_flagged.csv`, with the same columns.
- **Rank:** counted over both files, so a flagged match shows where it would have stood.
- **Job status:** `matched` if anything passed, else `no_match`.

**Re-runs:**
- Both match files are append-only.
- The match id hashes job, contractor and **scoring version**: model, templates, matcher version, every `matching` setting and, with `weights.lexical` on, the TF-IDF word-rarity fingerprint.
- Re-running with nothing changed adds nothing. Changing a setting adds a new set of rows; older rows, and any reviews of them, stay.
- With `weights.lexical` on, importing jobs or editing a profile changes word rarity, so the next `match` stores every shortlist again (about 200 rows). This also keeps each job's stored shortlist complete.

**Safety:** `match` refuses to run if the job and contractor vectors (texts or titles) come from different models or templates, or if a row's text changed since it was embedded. The message names the embed command to run.

`export-matches` writes `exports/matches-<time>.csv` for Excel. It holds the newest row per job and contractor, both lists (a `list` column), contractor names, job links and all score parts. It has a BOM, and formula-like cells are neutralized. **It holds names: keep it local.** Both exports carry the profession note when its vectors are current.

## Evaluation against human reviews

`import-reviewed-matches <sheet.csv>` reads a reviewed-match sheet export into `data/eval/reviewed_matches.csv` (`ReviewedMatch`):
- contract-type engagements only; anything starting with "Permanent", including contract-conversion targets, is left out;
- column names and tier spellings come from `config/reviewed_import.yaml`;
- talent names, contacts and outreach columns are never read;
- the reviewers' 100-point score is the sum of six sub-scores: skills 30, availability 15, location 15, contract likelihood 20, posting age 10, buyer urgency 10.

`evaluate-matches` (read-only) joins each label with the full ranking of its posting (`score_all_jobs`), matching postings by canonical URL. It prints and writes to `exports/evaluation-<time>.csv`:
- for each pair: our score, band, rank and shortlist status beside the reviewers' tier, total and skills score;
- the rank correlation (Spearman) with their total and their skills score;
- reviewer tier vs our band;
- for each job, the reviewers' A/B talents in our shortlist.

Pairs whose posting isn't an active job, or whose talent isn't in the contractor list, are listed with the reason. Our score has no availability, contract-likelihood, age or urgency part, so the skills score and tier are the fairer comparison.

## Running (Docker)

Every command runs inside the app container. CLAUDE.md rule 2 requires approval before running any of them.

```
docker compose build
docker compose run --rm app python -m jobmatch ingest-links data/inbox/links [--dry-run] [--refresh]
docker compose run --rm app python -m jobmatch ingest-manual data/inbox/manual [--dry-run]
docker compose run --rm app python -m jobmatch validate-data
docker compose run --rm app python -m jobmatch validate-data --data-dir data/samples
docker compose run --rm app python -m jobmatch build-contractors data/contractors/contractor-master-list.xlsx
docker compose run --rm app python -m jobmatch embed-contractors
docker compose run --rm app python -m jobmatch embed-jobs
docker compose run --rm app python -m jobmatch match [--dry-run]
docker compose run --rm app python -m jobmatch export-matches
docker compose run --rm app python -m jobmatch export-trial-run    # best exports.trial_run_per_job per job, for data_visualizer.ipynb
docker compose run --rm app python -m jobmatch import-reviewed-matches data/test_data_matches/raw.csv
docker compose run --rm app python -m jobmatch evaluate-matches
docker compose run --rm -e HF_HUB_OFFLINE=0 -e TRANSFORMERS_OFFLINE=0 app python -m jobmatch smoke-embed   # first run only: downloads the model (about 4.5 GB)
docker compose run --rm app pytest
docker compose run --rm app pytest -m model                    # real-model smoke test
docker compose run --rm app ruff check .
```

The embedding model is cached in the `hf-cache` Docker volume. After the first download, set `HF_HUB_OFFLINE=1` in `.env` so the container never contacts Hugging Face; the download command above overrides it for that one run. bge-m3's pinned revision ships `pytorch_model.bin`, and the library also fetches a converted `model.safetensors`, so the cache holds about 4.5 GB for it.

Dependencies are pinned in `uv.lock`, resolved for linux/amd64 only and limited to releases at least about 2 weeks old (`exclude-newer` in `pyproject.toml`). torch comes from the PyTorch CPU index, so no CUDA packages are installed. After changing dependencies, re-lock and run `docker compose build`.

## Storage conventions

- `data/contractors/contractors.csv` is built from the human-maintained workbook. The pipeline only reads it, and `build-contractors` backs up the old file before replacing it.
- `reviews.csv`, `runs.csv`, `matches.csv` and `matches_flagged.csv` are append-only. Every rewrite of another CSV is preceded by a timestamped copy in `data/backups/`.
- Each CSV jobmatch writes gets a `<name>.schema.json` sidecar recording its `schema_version` (jobs: version 3). After a schema change, rebuild with `scripts/reset_reimport.sh`.
- `data/raw/<source>-YYYY-MM.jsonl` keeps every posting as received: paste files, and the extracted `JobPosting` data or main page HTML for links. Identical records are written once.
- Multi-value cells use `|`. A literal `|` or `\` inside an item is escaped with a backslash.
- Vectors live in `data/embeddings/<namespace>.npz`, with model, version, dim, count and text-template hash in `data/embeddings/meta.json`.
