# jobmatch: contract job matching assistant

A local tool that collects **Canadian contract job postings**, matches each one to **our registered contractors** using a local AI model, and gives a **person** a ranked shortlist to review. The tool recommends; people decide.

Contract roles are often filled first-come, first-served, and checking job boards by hand all day is slow. jobmatch does the collecting, cleanup and first sorting so reviewers spend their time on judgment.

> **Status:** prototype (v0). Job intake, normalization, embeddings, matching, spreadsheet exports and evaluation against human reviews work. The review UI, automatic job-board feeds and the scheduler are not built yet.

---

## What this repo contributes

- **Job intake from links or pasted postings.** It reads job-board and career-page links (including LinkedIn links a person pastes), plus postings saved by hand as text, `.html` or `.pdf`. It respects robots.txt and never logs in or scrapes LinkedIn or Indeed.
- **Normalization for English and French postings.** It cleans the text, splits the posting into sections, and extracts title, company, location, work mode, language, contract type and length, seniority, years of experience, pay and skills. Rules live in editable YAML files in `config/`.
- **Deduplication and filtering.** The same job posted on several sites is stored once. Non-Canadian and non-contract postings are set aside, with the reason.
- **Contractor profiles from the master workbook.** It builds the contractor list from the talent spreadsheet and learns skills that the curated skill list doesn't know yet.
- **Local semantic matching.** It embeds jobs and contractors with `BAAI/bge-m3`, on the CPU and offline, then scores every pair on overall meaning, title, shared skills and type of work. Each pair gets a high, medium or low band, with explanations taken from the data: matched skills, missing must-have skills, flags.
- **Rule checks.** A seniority gap of two or more levels sends a match to a separate flagged list, with the reason.
- **Exports and evaluation.** Spreadsheet exports for reviewers, a notebook view of the best matches, and a comparison of the matcher's scores with human-reviewed matches to tune thresholds.
- **Safe storage with no database.** Plain CSV files with schema validation, atomic writes, file locks and timestamped backups. Vectors go in NumPy `.npz` files. Every step is safe to re-run.

## How it works

```
links / pasted postings ──► fetch ──► normalize ──► dedupe & filter ──► data/jobs.csv
                                                                             │
contractor workbook ──► build-contractors ──► contractors.csv                │
                                  │                                          │
                                  ▼                                          ▼
                          embed-contractors                              embed-jobs
                                  └──────────────► match ◄───────────────────┘
                                                     │
                       data/matches.csv  +  data/matches_flagged.csv
                                                     │
                     export-trial-run / export-matches ──► exports/*.csv ──► notebook / Excel ──► human review
```

## Requirements

- Docker with Docker Compose. The app runs in a `linux/amd64` container, CPU only, capped at 4 GB of memory.
- About 5 GB of disk for the embedding model (downloaded once into a Docker volume).
- Target machine: a 2019 Intel MacBook Pro with 16 GB RAM. Development also works on any x86_64 machine.
- Optional, for the results notebook: Jupyter and `pandas` on your own machine.

## Installation

```
cp .env.example .env
docker compose build
docker compose run --rm -e HF_HUB_OFFLINE=0 -e TRANSFORMERS_OFFLINE=0 app python -m jobmatch smoke-embed   # one-time model download
```

After the download, keep `HF_HUB_OFFLINE=1` in `.env`.

## Usage

The short version:

```
bash scripts/fetch_and_import.sh                                     # import jobs from data/inbox/
docker compose run --rm app python -m jobmatch embed-contractors     # after the contractor list changes
docker compose run --rm app python -m jobmatch embed-jobs
docker compose run --rm app python -m jobmatch match
docker compose run --rm app python -m jobmatch export-trial-run      # then open data_visualizer.ipynb
```

**See [doc/quick_start.md](doc/quick_start.md)** for every command, how to add jobs, reset and re-import, where results are stored, and how to read them.

## Project structure

```
doc/                 project documentation (quick start, development notes, context, change log)
config/              settings.yaml and YAML rules (skills, contract phrases, locations, normalization…)
src/jobmatch/
  cli.py             command line (python -m jobmatch <command>)
  sources/           link fetching, paste files, saved pages and PDFs, contractor workbook
  normalize/         one module per normalization step
  embed/             local embedding model and embedding texts
  match/             scoring, rule checks, exports, evaluation
  storage/           CSV store, NumPy vector store, repository interfaces
  llm/, review/      placeholders for later phases
scripts/             fetch_and_import.sh, reset_reimport.sh, rename_manual_templates.sh
data/                runtime data (git-ignored): inbox/, jobs.csv, matches*.csv, embeddings/, raw/
exports/             spreadsheets for people (git-ignored; they contain contractor names)
tests/               unit and end-to-end tests (synthetic data, network always mocked)
data_visualizer.ipynb   notebook for reading trial-run results
```

## Documentation

| File | For |
|---|---|
| [doc/quick_start.md](doc/quick_start.md) | Day-to-day use: commands, importing, running, reading results |
| [doc/DEVELOPMENT.md](doc/DEVELOPMENT.md) | Technical details: layout, normalization, embeddings, matching, storage |
| [doc/PROJECT_CONTEXT.md](doc/PROJECT_CONTEXT.md) | Goals, architecture and decisions |
| [doc/LOG-UPDATE.md](doc/LOG-UPDATE.md) | What each change solved, newest first |
| [doc/JTBD2-Full_pipeline.png](doc/JTBD2-Full_pipeline.png) | General pipeline template (not a v0 spec) |
| [doc/Project_repo_artifact.pdf](doc/Project_repo_artifact.pdf) | Project repo artifact (PDF) |
| [CLAUDE.md](CLAUDE.md) | Working rules for AI-assisted development (kept at the root so Claude Code loads it) |

## What it does not do (v0)

- **No emails.** Finding company contacts and drafting outreach come later, and a person will always review and send.
- **No LinkedIn or Indeed scraping**, and no logging in to any site. LinkedIn jobs enter only through links a person pastes. Indeed postings are pasted by hand.
- **No final decisions.** Every match is reviewed by a person.
- **No chatbot-style AI.** Matching uses embeddings and rules only. No contractor data is sent to outside AI services.

## Privacy and data

- Contractor profiles are personal information. They stay on the local machine, in git-ignored files. Logs and terminal output show talent IDs, never names.
- The exports in `exports/` contain names: don't share them outside the team.
- Use a password-protected laptop with disk encryption turned on.
- Don't edit the live CSV files while a command is running.

## Known limits

- Scores and thresholds are first guesses, tuned on a small set of reviewed matches. Expect some wrong suggestions and some misses early on.
- Pages that need JavaScript (Workday, Zoho Recruit, BambooHR) can't be fetched and must be pasted by hand.
- Contractors with thin profiles (no summary or strengths) are matched less reliably.
- Canadian anti-spam law (CASL) applies to any future outreach step and needs qualified advice before it is built.

## Glossary

- **Embedding:** turning text into numbers so a computer can tell which texts mean similar things.
- **Skill taxonomy:** our list of skills and their alternate spellings ("React" and "ReactJS" count as the same).
- **Band:** the confidence level of a match: high, medium or low.
- **Docker container:** a sealed package of the software that runs the same way on different computers.
