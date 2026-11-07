# jobmatch: contract job matching assistant

A local tool that collects **Canadian contract job postings**, asks **Gemini** (Google's AI, free tier) which of **our registered contractors** fit each one, and gives a **person** a ranked shortlist to review. Contractor names and contact details never leave the machine. The tool recommends; people decide.

Contract roles are often filled first-come, first-served, and checking job boards by hand all day is slow. jobmatch does the collecting, cleanup and first sorting so reviewers spend their time on judgment.

> **Status (2026-11-06):** v1 works end to end from the command line: job intake, normalization, matching with Gemini (`run-once`) and a spreadsheet export. The v0 local-embedding matcher was removed (kept on a separate local branch). Next: a desktop app (Mac `.app` and Windows `.exe`) for end users.

---

## What this repo contributes

- **Job intake from links or pasted postings.** It reads job-board and career-page links (including LinkedIn links a person pastes), plus postings saved by hand as text, `.html` or `.pdf`. It respects robots.txt and never logs in or scrapes LinkedIn or Indeed.
- **Normalization for English and French postings.** It cleans the text, splits the posting into sections, and extracts title, company, location, work mode, language, contract type and length, seniority, years of experience, pay and skills. Rules live in editable YAML files in `config/`.
- **Deduplication and filtering.** The same job posted on several sites is stored once. Non-Canadian and non-contract postings are set aside, with the reason.
- **Contractor profiles from the master workbook.** It builds the contractor list from the talent spreadsheet and learns skills that the curated skill list doesn't know yet.
- **Matching with Gemini.** One call per job judges every contractor (fit, partial or no, with a reason) and describes the best ones: requirements met and missing, red flags, and evidence quoted from the posting and profile, each quote checked word for word. Contractors are sent as anonymous cards, never with names, contact details, location or rates.
- **Quota guard.** Every call's token use is logged, and a run that would use most of the day's free quota asks first. Answers are cached, so re-running costs nothing.
- **Contractor loading.** Load the master workbook or a contractor CSV: new contractors are added and changed ones updated.
- **Exports and evaluation.** A spreadsheet of the shortlists for reviewers, and a comparison of Gemini's verdicts with blind-labelled pairs.
- **Safe storage with no database.** Plain CSV files with schema validation, atomic writes, file locks and timestamped backups. Every step is safe to re-run.

## How it works

```
links / pasted postings ──► fetch ──► normalize ──► dedupe & filter ──► data/jobs.csv
                                                                             │
contractor workbook / CSV ──► contractors.csv ──► anonymous cards            │
                                                       │                     │
                                                       ▼                     ▼
                                         Gemini: one call per job (quota-checked, cached)
                                                       │
                                               data/llm_matches.csv
                                                       │
                                  export-llm-matches ──► exports/*.csv ──► human review
```

## Requirements

- Docker with Docker Compose (until the desktop app ships). The app runs in a small `linux/amd64` container, capped at 4 GB of memory.
- A Gemini API key from Google AI Studio (free tier), in `.env`.

## Installation

```
cp .env.example .env      # then add GEMINI_API_KEY=
docker compose build
```

## Usage

```
docker compose run --rm app python -m jobmatch llm-judge --dry-run   # how many calls, quota check
docker compose run --rm app python -m jobmatch run-once              # add --contractors <file> when the list changed
docker compose run --rm app python -m jobmatch export-llm-matches
```

**See [doc/quick_start.md](doc/quick_start.md)** for every command, how to add jobs, and how to read the results.

**Desktop app (for end users, no Docker):** an Intel Mac `.app` and a Windows `.exe`, built by GitHub Actions (Actions › build-app). Installing and first use: [doc/FIRST_LAUNCH.md](doc/FIRST_LAUNCH.md). Preview it from the repo: `docker compose run --rm --service-ports app python -m jobmatch.app --browser --host 0.0.0.0`, then http://localhost:8080.

## Project structure

```
doc/                 project documentation (quick start, development notes, context, change log)
config/              settings.yaml and YAML rules (skills, contract phrases, locations, normalization…)
src/jobmatch/
  cli.py             command line (python -m jobmatch <command>)
  sources/           link fetching, paste files, saved pages and PDFs, contractor workbook
  normalize/         one module per normalization step
  llm/               Gemini matching: anonymized cards, prompt, judge, quota guard, shortlists
  pipeline.py        run-once: the whole pipeline in one call
  storage/           CSV store, repository interfaces
  review/            placeholder (desktop app next)
scripts/             fetch_and_import.sh, reset_reimport.sh, rename_manual_templates.sh
data/                runtime data (git-ignored): inbox/, jobs.csv, llm_matches.csv, llm_results/, raw/
exports/             spreadsheets for people (git-ignored; they contain contractor names)
tests/               unit and end-to-end tests (synthetic data, network always mocked)
```

## Documentation

| File | For |
|---|---|
| [doc/FIRST_LAUNCH.md](doc/FIRST_LAUNCH.md) | End users: installing the desktop app and first use |
| [doc/quick_start.md](doc/quick_start.md) | Day-to-day use: commands, importing, running, reading results |
| [doc/DEVELOPMENT.md](doc/DEVELOPMENT.md) | Technical details: layout, intake, normalization, matching, storage |
| [doc/PROJECT_CONTEXT.md](doc/PROJECT_CONTEXT.md) | Goals, architecture and decisions |
| [doc/LOG-UPDATE.md](doc/LOG-UPDATE.md) | What each change solved, newest first |
| [doc/V1_PLAN.md](doc/V1_PLAN.md) | v1 design: Gemini matching, privacy, quota |
| [doc/PROTOTYPES.md](doc/PROTOTYPES.md) | What v0 tried and decided (full log in `doc/archive/`) |
| [doc/LABELLING.md](doc/LABELLING.md) | The blind labelling sheet: why, how to fill it in, how to import it |
| [doc/JTBD2-Full_pipeline.png](doc/JTBD2-Full_pipeline.png) | General pipeline template (not a v0 spec) |
| [doc/Project_repo_artifact.pdf](doc/Project_repo_artifact.pdf) | Project repo artifact (PDF) |
| [CLAUDE.md](CLAUDE.md) | Working rules for AI-assisted development (kept at the root so Claude Code loads it) |

## What it does not do

- **No emails.** Finding company contacts and drafting outreach come later, and a person will always review and send.
- **No logging in to any site.** LinkedIn jobs enter only through links a person pastes; pages that need a login or JavaScript are pasted by hand.
- **No final decisions.** Every match is reviewed by a person.
- **No personal data to the AI.** Gemini sees anonymous contractor cards only: no names, contact details, location, rates or IDs.

## Privacy and data

- Contractor profiles are personal information. They stay on the local machine, in git-ignored files. Logs and terminal output show talent IDs, never names.
- The exports in `exports/` contain names: don't share them outside the team.
- Use a password-protected laptop with disk encryption turned on.
- Don't edit the live CSV files while a command is running.

## Known limits

- Gemini is strict about "different profession" and sometimes contradicts itself; contractors it judged fit or partial without describing them are still shown ("reason only") for a person to check.
- Free-tier limits change without notice. The limits in `config/settings.yaml` are guesses until copied from AI Studio.
- Pages that need JavaScript (Workday, Zoho Recruit, BambooHR) can't be fetched and must be pasted by hand.
- Contractors with thin profiles (no summary or strengths) are matched less reliably.
- Canadian anti-spam law (CASL) applies to any future outreach step and needs qualified advice before it is built.

## Glossary

- **Verdict:** Gemini's judgment of a contractor for a job: fit, partial or no.
- **Quota:** how many Gemini requests the free tier allows per day.
- **Docker container:** a sealed package of the software that runs the same way on different computers.
