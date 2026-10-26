# Development notes (jobmatch)

Technical companion to the plain-language [README.md](README.md). Goals and architecture: [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md). Working rules: [CLAUDE.md](CLAUDE.md). What each change solved: [LOG-UPDATE.md](LOG-UPDATE.md).

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
- **Phase 4 (started):**
  - contractor list built from the master workbook (`build-contractors`)
  - job and contractor vectors (`embed-jobs`, `embed-contractors`) with `BAAI/bge-m3`
- **Not built yet:** Adzuna/API sources, the matcher and evaluation script (rest of phase 4), review UI (phase 5), the scheduler (phase 6).
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

**How fetching behaves** (config in `config/sources.yaml`):
- plain GET only: no cookies, logins or JavaScript
- 2 s between requests; robots.txt respected; timeout, size and redirect limits
- Indeed is never fetched (CLAUDE.md rule 6); LinkedIn links a person pastes are allowed
- **a page that's gone** (HTTP 404/410) is dropped. It's recorded in `data/raw/`, never fetched again (`--refresh` retries), and doesn't count as an error
- **other failures** (timeouts, 429, 5xx) are reported as errors and retried on the next run
- **page encoding:** the HTTP header's charset, else the page's `<meta charset>`, else UTF-8 with a Windows-1252 fallback

## Contractor list

`python -m jobmatch build-contractors data/contractors/contractors_master_list.xlsx` builds `data/contractors/contractors.csv`:
- It joins the Talent and Talent Profiles tabs on Talent ID, keeping only rows with `Contract? = Yes`.
- The rules and column names are in `config/contractor_import.yaml`.
- `.xlsx` is read with the standard library (`sources/xlsx.py`), with no openpyxl or pandas.
- An existing output is copied to `data/contractors/backups/` first.
- Only counts are printed or logged, never names.

This file isn't yet the pipeline's `data/contractors.csv` (the `Contractor` schema); mapping it is part of phase 4. For now, `embed-contractors` reads it directly (`paths.contractors_csv`).

## Embeddings

`embed-contractors` and `embed-jobs` turn each contractor and each active job (`new`, `matched`, `no_match`) into one 1024-dimension vector with `BAAI/bge-m3` (pinned revision in `settings.yaml`, CPU only, no prefixes, normalized, cut at 512 tokens).

The text embedded for each row is built in `embed/texts.py`. Both sides use the same labels in the same order, most useful first, because the tail is cut:

| Label | Contractor | Job |
|---|---|---|
| `Title` | `standardized_headline` | `title_norm` |
| `Target titles` | `target_titles` | — |
| `Seniority` | `seniority` | `seniority` |
| `Skills` | taxonomy skills found in the profile, then remaining `core_strengths` | `skills` |
| `Industries` | `main_industry` | — |
| `Responsibilities` / `Requirements` / `Nice to have` | — | section lists (else `Description` from `description_clean`) |
| `Summary` / `Proof points` | `executive_summary`, `proof_points` | — |

Names, IDs, location, pay or rate, hours, work mode, contract type and company are left out: they are gates in matching, not meaning, and names are personal data.

Re-runs are incremental (`embed/build.py`): a vector is reused when the row's text hash, model, model version and template are unchanged; rows that are gone are dropped. Editing a template means bumping `TEMPLATE_VERSION` in `texts.py`, which re-embeds everything. The exact texts are written to `data/embeddings/<namespace>_texts.jsonl` for checking.

## Running (Docker)

Every command runs inside the app container. CLAUDE.md rule 2 requires approval before running any of them.

```
docker compose build
docker compose run --rm app python -m jobmatch ingest-links data/inbox/links [--dry-run] [--refresh]
docker compose run --rm app python -m jobmatch ingest-manual data/inbox/manual [--dry-run]
docker compose run --rm app python -m jobmatch validate-data
docker compose run --rm app python -m jobmatch validate-data --data-dir data/samples
docker compose run --rm app python -m jobmatch build-contractors data/contractors/contractors_master_list.xlsx
docker compose run --rm app python -m jobmatch embed-contractors
docker compose run --rm app python -m jobmatch embed-jobs
docker compose run --rm -e HF_HUB_OFFLINE=0 -e TRANSFORMERS_OFFLINE=0 app python -m jobmatch smoke-embed   # first run only: downloads the model (about 4.5 GB)
docker compose run --rm app pytest
docker compose run --rm app pytest -m model                    # real-model smoke test
docker compose run --rm app ruff check .
```

The embedding model is cached in the `hf-cache` Docker volume. After the first download, set `HF_HUB_OFFLINE=1` in `.env` so the container never contacts Hugging Face; the download command above overrides it for that one run. bge-m3's pinned revision ships `pytorch_model.bin`, and the library also fetches a converted `model.safetensors`, so the cache holds about 4.5 GB for it.

Dependencies are pinned in `uv.lock`, resolved for linux/amd64 only and limited to releases at least about 2 weeks old (`exclude-newer` in `pyproject.toml`). torch comes from the PyTorch CPU index, so no CUDA packages are installed. After changing dependencies, re-lock and run `docker compose build`.

## Storage conventions

- `contractors.csv` and `contractor_facets.csv` are maintained by humans. jobmatch only reads them.
- `reviews.csv` and `runs.csv` are append-only. Every rewrite of another CSV is preceded by a timestamped copy in `data/backups/`.
- Each CSV jobmatch writes gets a `<name>.schema.json` sidecar recording its `schema_version` (jobs: version 3). After a schema change, rebuild with `scripts/reset_reimport.sh`.
- `data/raw/<source>-YYYY-MM.jsonl` keeps every posting as received: paste files, and the extracted `JobPosting` data or main page HTML for links. Identical records are written once.
- Multi-value cells use `|`. A literal `|` or `\` inside an item is escaped with a backslash.
- Vectors live in `data/embeddings/<namespace>.npz`, with model, version, dim, count and text-template hash in `data/embeddings/meta.json`.
