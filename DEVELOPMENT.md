# Development notes (jobmatch)

Technical companion to the plain-language [README.md](README.md). Goals and architecture: [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md). Working rules: [CLAUDE.md](CLAUDE.md).

## Status

Build phase 1 (skeleton) is in place:

- config loading (`config/*.yaml`), validated with pydantic
- pydantic schemas for jobs, contractors, facets, matches, reviews and runs
- repository interfaces plus the CSV store (atomic writes, file locks, backups, header and row validation) and the NumPy vector store
- the `validate-data` and `smoke-embed` commands
- Docker image (CPU only, linux/amd64) and Compose service with a 4 GB memory cap

Build phase 2 (normalization) is in place: `jobmatch.normalize.pipeline.Normalizer` turns a `RawJob` into a `Job` (EN/FR cleanup, boilerplate removal, title/company/location/language/contract/skill/seniority extraction, fingerprint, hard filters with `filter_reason`). Rules live in `config/normalization.yaml`, `config/canada_locations.yaml`, `config/contract_phrases.yaml` and `config/skills_taxonomy.yaml`; thresholds in the `normalize:` section of `settings.yaml`. Test fixtures are synthetic postings in `tests/fixtures/postings/`.

Sources, dedupe, matching and the review UI come in later phases (PROJECT_CONTEXT.md section 11).

## Layout

```
config/            settings.yaml and the YAML taxonomies (skills, contract phrases, company aliases)
src/jobmatch/
  cli.py           Typer CLI (python -m jobmatch ...)
  config.py        settings and taxonomy loaders
  schemas.py       pydantic models for every record crossing a module boundary
  storage/         repository interfaces, CSV store, NumPy vector store, atomic file helpers
  embed/           local sentence-transformers encoder and smoke check
  sources/ normalize/ match/ review/ llm/   placeholders for later phases (llm/ stays empty in v0)
data/              runtime data (git-ignored, mounted into the container)
  samples/         synthetic sample contractors (committed)
tests/
```

## Running (Docker)

All commands run inside the app container and need approval before they run (CLAUDE.md rule 2):

```
docker compose build
docker compose run --rm app python -m jobmatch validate-data
docker compose run --rm app python -m jobmatch validate-data --data-dir data/samples
docker compose run --rm app python -m jobmatch smoke-embed   # downloads the model on first run (about 470 MB)
docker compose run --rm app pytest
docker compose run --rm app pytest -m model                    # real-model smoke test
docker compose run --rm app ruff check .
```

The embedding model is cached in the `hf-cache` Docker volume. After the first download, set `HF_HUB_OFFLINE=1` in `.env` so the container never contacts Hugging Face.

Dependencies are pinned in `uv.lock`, resolved for linux/amd64 only. torch comes from the PyTorch CPU index, so no CUDA packages are installed.

## Storage conventions

- `contractors.csv` and `contractor_facets.csv` are maintained by humans. jobmatch only reads them.
- `reviews.csv` and `runs.csv` are append-only. Every rewrite of another CSV is preceded by a timestamped copy in `data/backups/`.
- Each CSV jobmatch writes gets a `<name>.schema.json` sidecar recording its `schema_version`.
- Multi-value cells use `|`. A literal `|` or `\` inside an item is escaped with a backslash.
- Vectors live in `data/embeddings/<namespace>.npz`, with model, version, dim, count and text-template hash in `data/embeddings/meta.json`.
