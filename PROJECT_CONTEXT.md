# PROJECT_CONTEXT.md: jobmatch (preliminary context for Claude Code)

Status: build phases 1 (skeleton) and 2 (normalization) done on 2026-10-19; phase 3 manual intake (posting links plus paste files) done on 2026-10-20, with fuzzy dedupe on 2026-10-21; phase 4 started on 2026-10-22 (contractor list, job and contractor vectors with bge-m3), and the matcher was built on 2026-10-26 (evaluation script waits for reviewer data). All of it is verified on the dev machine only, not yet on the client Mac. Change summaries for teammates are in `LOG-UPDATE.md`. Other estimates and model choices below are starting assumptions, not verified facts. Decisions made since the first draft are logged in section 13. Rules for how you work are in `CLAUDE.md`; follow them.

## 1. Problem

Contractors constantly look for contract work, and companies constantly hire contractors. We have a **precompiled list of contractors** with descriptions of their expertise. The job is to **discover Canadian contract postings quickly** (first come, first served), **match each posting to the best-fit contractors**, and put the best matches in front of a **human**, who approves or rejects. Later (not in the prototype) the system will find the company's HR contact and draft a personalized outreach email from a sample template. The system never sends email; a human always sends.

Scope: **public job sources in Canada only.** Postings in English and French.

## 2. Prototype scope (v0)

In scope:
1. Job intake from (a) job-board APIs and feeds, and (b) manual human input for LinkedIn jobs.
2. Normalize, filter and dedupe jobs into one canonical schema.
3. Local embeddings for jobs and contractors, plus rule-based skill/constraint scoring, producing a ranked best-k per job.
4. File-based storage (CSV tables plus NumPy embedding files) for contractors, jobs, embeddings, matches and reviews, behind repository interfaces.
5. Human review: approve / reject (with reason) / needs-info.
6. Runs as a CLI that can be scheduled (launchd on the client's Mac; cron or Windows Task Scheduler elsewhere).

Out of scope for v0: **any LLM (no LLM judge)**, contact discovery, email drafting or sending, any LinkedIn/Indeed automation, paid APIs, cloud hosting. The email block is only kept in mind for the architecture (a later `outreach/` module reading approved matches). An LLM judge is a possible future addition (section 9).

## 3. Key decisions already made

- Language: **Python 3.12**.
- **Hardware target (client machine): MacBook Pro 13-inch (2019), Intel Core i5 1.4 GHz quad-core, 16 GB LPDDR3 RAM, Intel Iris Plus 645 graphics, macOS on Intel x86_64.** CPU-only; the integrated GPU is not usable for ML. Every model, library and batch size must be chosen for this CPU. Report model size and memory in cost estimates. See section 3a for Intel-Mac constraints.
- **Development machine:** Windows PC, AMD Ryzen 9 9950X3D, NVIDIA RTX 5070 Ti, 32 GB DDR5 RAM (x86_64). It is much faster than the client's Mac and has a GPU the client lacks: **never use the GPU** (no CUDA dependencies or code paths, no `--gpus`), develop and test in CPU-only containers, and treat any timing measured on the dev machine as optimistic; benchmark on the client Mac.
- Storage: **no database in v0.** Data lives in **CSV files** (human-readable tables) and **NumPy `.npz` files** (embeddings) in a `data/` folder mounted as a Docker volume (section 8a). Similarity is computed in-process with NumPy (a few hundred contractors and thousands of jobs need no vector index). All access goes through repository interfaces (`JobRepository`, `ContractorRepository`, `MatchRepository`, `ReviewRepository`, `VectorStore`), so MongoDB or SQLite can replace the CSV layer later without touching matching code. MongoDB was considered and deferred to keep v0 lightweight.
- AI is **local only** in v0: pretrained multilingual embeddings via `sentence-transformers`, zero-shot, no training.
- **No LLM in v0.** The LLM judge is deferred. Explanations come from data, not generated text.
- Final deliverable: a **Python package with a CLI** run by a scheduler, plus a small local **Streamlit review UI**. Not an EXE (section 10).
- The human always makes the final call.

## 3a. Intel Mac platform constraints (from web research in October 2026; re-verify before relying)

- **PyTorch publishes no macOS x86_64 wheels after 2.2.2** (2.3.0 and later are Apple-silicon only). Torch 2.2.2 needs Python 3.12 or lower and `numpy<2`, and it is incompatible with very recent `transformers` releases (an Open WebUI report showed an import crash), so a native install needs older `transformers` / `sentence-transformers` pins found by testing.
- **ONNX Runtime dropped macOS x86_64 wheels from 1.24** (1.23.x is the last). A native ONNX path needs `onnxruntime<1.24`, and the chosen model must exist in the runtime or catalog used. Other ML packages have also dropped Intel-Mac wheels.
- **Default runtime decision (chosen by the user): Docker Compose with a Linux x86_64 image for the app.** On an Intel Mac this runs at native CPU speed (no emulation) and allows current dependency versions. **Fallback:** a native venv with a frozen lockfile (Python 3.12, `torch==2.2.2`, `numpy<2`, tested pins). Use the fallback only if Docker proves unworkable on the client Mac, and ask first.
- Docker Desktop must support this Mac's macOS version (a 2019 model likely cannot run the newest macOS, so check Docker Desktop's current system requirements and licensing terms for the client's organization size). Cap the Docker VM memory (about 4 GB).
- The development machine (Windows, AMD Ryzen 9, x86_64) has the same CPU architecture as the client's Intel Mac, so `linux/amd64` images built locally run unchanged on the client. If anyone builds on Apple silicon instead, build for `linux/amd64`.
- Expect thermal throttling on this laptop under sustained load; keep batch jobs short.

## 4. Pipeline

```
sources (APIs/feeds + manual intake)
 -> normalize -> dedupe -> hard filters (Canada, contract signal, freshness, language)
 -> build embedding text -> embed job (local, CPU)
 -> score vs contractor facet vectors (cosine, NumPy) + skill-overlap + rule gates
 -> top-k (default 5) -> confidence band (high / medium / low)
 -> store matches (pending_review) -> human review (approve / reject + reason / needs info)
 -> [later] contact discovery -> email draft -> human sends
```

Principles: favor recall in the prefilter (a false positive costs seconds of review, a miss costs an opportunity); precision comes from the rule gates and the human reviewer; log every filtered-out posting with its reason for tuning; queue for the human immediately, enrich later.

## 5. Sources

- **Job-board APIs:** Adzuna (free key, Canada endpoint), possibly Jooble; **Job Bank** (jobbank.gc.ca; no official search API known, so scraper or export, to be decided); optional public **ATS feeds** (Greenhouse, Lever, SmartRecruiters and similar) for a curated list of Canadian employers. Mind rate limits (a free Adzuna tier may be around 1,000 calls per month; verify) and each source's terms.
- **Manual (LinkedIn):** a human pastes jobs through the intake modes below. No automated LinkedIn access.
- **Built (2026-10-20): posting links.**
  - A person adds posting URLs (any board, including LinkedIn) to `data/inbox/links/*.txt`.
  - `ingest-links` fetches each new link once, reads the schema.org `JobPosting` block (falling back to the page's main text), and caches the result in `data/raw/`.
  - Indeed is never fetched (CLAUDE.md rule 6). Paste files (`data/inbox/manual/`) remain the fallback for Indeed and for pages that cannot be fetched.
  - This replaces the planned paste-page mode below as the main manual path.
- Every source implements one `JobSource` interface and returns raw records; normalization is shared.

### Manual intake modes (no LLM)
1. **Paste-page mode:** human pastes the whole job page text plus the URL; **rule-based extraction** (regex and section heuristics for common LinkedIn page text patterns: title line, company, location, employment type, "About the job" description) pre-fills a form; the human corrects and confirms in the UI. Unparseable fields are left blank for the human.
2. **Structured form:** title, company, location, URL, employment type, description.
3. **Bulk CSV/JSON template** for several jobs at once.
4. **Stub records** for LinkedIn dashboard or alert lists that show only title, company, location and link: stored as `status = needs_details`; a cheap title/company skill score decides which stubs are worth pasting full details for; the same job found through an API source can fill the stub automatically (match on company_norm + title_norm).

## 6. Canonical job schema (`data/jobs.csv`)

`source`, `source_id`, `url` (canonical), `title_raw`, `title_norm`, `company_raw`, `company_norm`, `location_raw`, `city`, `province` (ISO 3166-2:CA), `country`, `remote_type` (onsite/hybrid/remote/unknown), `employment_type` (contract/permanent/temporary/unknown), `contract_signals` (matched phrases), `duration_months`, `language` (en/fr/bilingual), `description_raw`, `description_clean`, `requirements` (list), `skills` (normalized list), `seniority`, `pay` (min, max, unit, currency), `noc_code`, `posted_at`, `first_seen_at`, `fingerprint`, `embedding` {model, version, dim, vector}, `status` (new / needs_details / filtered_out / matched / no_match), `filter_reason`.

Uniqueness on (`source`, `source_id`) is enforced in code; fingerprints are loaded into an in-memory set at the start of each run. List-valued fields share one cell joined with `|` (conventions in section 8a).

### Standardization techniques
- HTML to text (BeautifulSoup + lxml), Unicode NFKC normalization (ftfy for mojibake), whitespace cleanup.
- **Boilerplate stripping:** drop sections by header heuristics ("About us", "Benefits", "Equal opportunity", French equivalents) before embedding.
- **Title normalization:** remove requisition IDs, location suffixes, "(Contract)", gender markers such as (m/f/d) and "H/F"; casefold copy for matching.
- **Company normalization:** strip Inc., Ltd., Corp., Ltée, etc.; casefold; alias table in `company_aliases.yaml`.
- **Location parsing:** Canadian city/province lookup, "Remote - Canada" handling, keep `location_raw`.
- **Language detection** on description (e.g. lingua); bilingual flag when both languages are present.
- **Contract detection:** structured employment-type field first, then weighted phrase list in `contract_phrases.yaml` (English and French: contract, fixed-term, mat leave, CDD, durée déterminée, mandat, freelance, etc.), with down-weights for permanent signals and false positives such as "contract management".
- **Skill extraction:** YAML taxonomy with synonyms (ReactJS, React.js -> react) matched with rapidfuzz or a phrase matcher. This taxonomy is central in v0 because it replaces LLM extraction; plan time to build and maintain it.
- **Dedupe:** exact (source + source_id, canonical URL), fingerprint hash of (company_norm, title_norm, city), fuzzy (rapidfuzz token-set ratio on titles within the same company), and near-duplicate embeddings (very high cosine within the same company in a recent window).

## 7. Contractors

Schema (`data/contractors.csv` plus `data/contractor_facets.csv`, with one facet per row linked by `contractor_id`): `contractor_id`, `display_name` (personal data), `profile_text_raw`, `profile_text_clean`, `facets` (list of {area, skills, years, seniority, domains}), `languages`, `regions` and remote preference, `noc_codes`, `rate_range` (optional), `profile_hash`; embeddings live in `data/embeddings/` (one vector per facet, plus one for the whole profile, keyed by content hash, with model/version/dim in a metadata file).

Technique: without an LLM, facets are built from a **human-fillable template** (CSV or YAML: area, skills, years, seniority, domains) and checked against the skill taxonomy; the skill matcher can propose skills from the raw description for the human to confirm. Embed each facet from a **fixed text template** so jobs and contractors compare like with like. A contractor's score for a job is the **max over facets**. Re-embed when `profile_hash` or the embedding model changes. Contractor data is personal information: keep it local and out of logs.

## 8. Matching (no LLM)

- **Embedding text template (same for jobs and facets):** `Title: ... | Skills: ... | Domain: ... | Seniority: ... | Summary: <requirements-first cleaned text, truncated to the model's limit>`. E5-family models need `query:` / `passage:` prefixes; check the model card.
- **Model in use since 2026-10-22: `BAAI/bge-m3`** (see section 13). The original reasoning follows. **Model (CPU-friendly, configurable):** start with `intfloat/multilingual-e5-small` (roughly 118M parameters, well under 1 GB in memory; the lightest sensible option for this laptop's 1.4 GHz Intel CPU, with roughly tens to a few hundred milliseconds per text as an estimate to measure). Consider `multilingual-e5-base` if recall is poor. `BAAI/bge-m3` (roughly 560M parameters, longer inputs) is heavier on CPU: benchmark it before adopting. Multilingual is required for French postings. A lighter ONNX runtime path is an optional later optimization, but on Intel macOS it needs `onnxruntime<1.24` and a model available in that runtime's catalog (verify first).
- **As built (2026-10-26):** see section 13 and `DEVELOPMENT.md` (Matching). The original sketch follows.
- **Score:** weighted combination of cosine similarity and skill overlap. **Rule gates** (configurable): must-have skills present, seniority compatible, location/remote compatible, language compatible, contract signal present. Thresholds live in `settings.yaml` and are tuned on reviewer decisions. Top-k default 5.
- **Explainability shown to the human (all data-derived):** similarity score, matched facet, matched skills, missing must-have skills, contract phrases found, which gates passed or failed, confidence band.
- **Review queue ordering:** by confidence band then score; lower bands still shown (recall first).
- **Evaluation:** a script computing recall and precision against reviewer-labeled pairs; use it to set thresholds and to compare embedding models.
- Cosine similarity can rank related-but-wrong skills highly (for example Java vs JavaScript): keep must-have skills in the rule gate.

## 8a. Data storage: CSV and NumPy files (no database in v0)

Layout under `data/` (a Docker volume; never overwrite without a backup):
- `contractors.csv`, `contractor_facets.csv`: human-maintained inputs. The tool only reads them.
- `jobs.csv`: normalized jobs. New rows are appended; status changes rewrite the file atomically.
- `matches.csv`: one row per shortlisted (job, contractor, facet): `match_id`, `job_id`, `contractor_id`, `facet`, `cosine`, `skill_overlap`, `score`, `band`, `matched_skills`, `missing_must_have`, `gates_failed`, `created_at`.
- `reviews.csv`: **append-only** reviewer decisions: `match_id`, `decision` (approved / rejected / needs_info), `reason_code`, `notes`, `reviewer`, `reviewed_at`. The current status of a match is its latest review. This file doubles as the evaluation dataset. Reason codes: wrong_skills, seniority, location, language, not_contract, duplicate, expired, other.
- `runs.csv`: per-run stats (counts per source, filtered, matched, errors, durations, model and version).
- `embeddings/*.npz` plus `embeddings/meta.json`: float32 vectors with ids and content hashes, and the model name, version, dimension and text-template hash. Re-embed only rows whose content hash or model changed. Vectors do not belong in CSV.
- `backups/`: timestamped copies taken before any rewrite of a CSV.
- `raw/<source>-YYYY-MM.jsonl` (phase 3): **append-only** raw records exactly as fetched or pasted, one JSON object per line with source, source_id, fetched_at and payload. Feeds the later HR-contact step (the "raw DATABASE" in the pipeline diagram) and allows re-normalizing when rules change.

**CSV parser/store module** (`storage/csv_store.py`), standard-library `csv` plus pydantic (no pandas in v0 unless approved):
- Read with `encoding="utf-8-sig"` (tolerates Excel's BOM) and `newline=""`; write UTF-8 with LF in internal files, with a BOM only in human-facing exports.
- Validate headers and every row against the pydantic models, with errors naming the file and row number; record a `schema_version` in a sidecar so changes can be migrated. A `validate-data` command runs these checks.
- Conventions: multi-value cells joined with `|`; booleans `true`/`false`; timestamps ISO 8601 UTC; IDs and NOC codes stay **strings** (Excel strips leading zeros, and some NOC codes start with 0); an empty cell means null; commas and newlines in text are quoted by the `csv` module.
- **Atomic writes:** write a temp file in the same folder, fsync, then `os.replace`, under a file lock (for example `filelock`) around every read-modify-write. One writer at a time: the scheduled run and the review UI both go through the store inside the container.
- **CSV/formula injection:** posting text is untrusted. In any human-facing export meant for Excel or Numbers, neutralize cells beginning with `=`, `+`, `-`, `@`, tab or carriage return; keep internal files unsanitized.
- Humans should not open the live `data/` CSVs in Excel while a run is active (Excel can lock files on Windows and rewrite formatting); use exported copies.
- Dedupe, lookups and joins happen in memory (loaded at run start), which is fine for thousands to tens of thousands of rows. Retention: archive jobs older than N days (default 90) into `jobs_archive_YYYY-MM.csv`; keep reviews forever.
- **When to move on:** concurrent writers, frequent row-level updates, cross-file queries or growth beyond tens of thousands of jobs. Then swap the repository implementation to SQLite (single file, no server) or MongoDB.

## 9. Future (not v0): optional LLM judge

If added later, behind an `LLMProvider` interface: input = posting + top-k facets; output = JSON verdicts (strong/possible/reject, reason, matched/missing requirements, red flags, evidence quotes), temperature 0, pydantic-validated. On this hardware, only small local models are realistic (roughly 4B parameters or less, quantized) and CPU inference may take tens of seconds per posting, so it would run only on the shortlist, or optionally through free Groq/Gemini (disabled by default; review data terms; never send contractor personal data without explicit approval). Decide this only after v0 has produced reviewer data showing whether the rule gates and embeddings are not enough.

## 10. Packaging and scheduling

- Python package (`pyproject.toml`), CLI built with Typer, review UI in Streamlit. The app runs in a Docker container (Docker Compose) with `data/` mounted as a volume; there is no database service in v0.
- **Not an EXE** for v0: bundling PyTorch plus models into an EXE is huge and fragile. Later options: a Docker Compose bundle, or a PyInstaller build of the review UI only.
- **Scheduling on macOS:** a **launchd** LaunchAgent (preferred over cron on macOS) with a 10 to 15 minute interval running `docker compose run --rm app python -m jobmatch run-once` (or the native equivalent). Runs take a lock file, are idempotent and log to a file. A sleeping or lid-closed laptop skips runs: keep it plugged in with sleep disabled on power (or use `caffeinate`), or use an always-on machine for real use.
- Latency: on this 1.4 GHz Intel laptop, expect tens to a few hundred milliseconds to embed a job text with a small model (an estimate; measure it); contractors are embedded once. Without an LLM step, a full run per batch of jobs should be fast. Measure on the real machine.

## 11. Suggested build phases (each needs a cost report and approval)

1. Repo skeleton, config, pydantic schemas, repository interfaces with the CSV store and NumPy vector store (atomic writes, locking, backups, `validate-data`), Dockerfile and Docker Compose (app with `data/` volume and capped memory), and a smoke test that the embedding model loads and encodes on the target Mac.
2. Normalization modules with tests (English and French fixtures), skill taxonomy v1, contract phrase list v1.
3. Adzuna source, manual intake (paste-page with rule-based extraction, form, CSV), stubs and dedupe.
4. Contractor template import and facet embedding; job embedding; NumPy scorer with rule gates; evaluation script.
5. Streamlit review UI with approve/reject/needs-info and reason codes, plus reviewer-decision logging.
6. Scheduler wrapper (lock, logging) and run reports.
7. Later: Job Bank source, ATS feeds, threshold tuning and few-shot ideas from reviewer data, optional LLM judge, then contact discovery and email drafting.

## 12. Assumptions and open questions

- **Reminder, fix before real use:** `normalize.freshness_days` is 999 (the stale filter is off) so the matching algorithm can be tested on older postings. Set a real limit (30 was the starting guess). Also decide on an "expired" filter from the posting's `validThrough` date; it was deferred on 2026-10-21.
- Known: the client machine is a 2019 13-inch MacBook Pro (Intel i5 1.4 GHz quad-core, 16 GB RAM, Intel graphics). Development machine (decided): Windows PC with AMD Ryzen 9 9950X3D, RTX 5070 Ti and 32 GB RAM, x86_64 like the client's Mac. Docker is the chosen runtime. Still to confirm: the client Mac's macOS version and Docker Desktop support, free disk space, whether it can stay on and plugged in, and Docker Desktop licensing for the client's organization size.
- How many contractors, and what does a typical description look like (length, structure, number of distinct specialties)?
- How many labeled examples or historical selections exist?
- Which Job Bank access method is acceptable (own scraper, export, third-party actor)?
- Expected posting volume per day after hard filters.
- Who maintains the skill taxonomy and contract phrase list, and how often?
- Compliance: CASL for outreach later, and privacy obligations (PIPEDA) for contractor data. Not legal advice; get qualified review.
- Backup retention: `data/backups/` grows without limit (every `jobs.csv` status rewrite adds a copy). Deleting old backups is destructive, so a retention policy needs an explicit decision.

## 13. Decisions log

**2026-10-19**
- **Pipeline diagram** (`JTBD2-Full_pipeline.png`) is a general template for data flow, not a v0 spec. Its blocks map to the build phases: job intake and filtering (phases 2–3), embedding and DATABASE (phase 1, CSV + NumPy), summary/context = rule-based embedding text template (phase 4), matching (phase 4), human decision (phase 5). The "Human / LLM" decision makers are to be finalized later; v0 is human only, no LLM. HR-contact search, outreach email and send are out of scope for v0, and a human always sends.
- **Contractor sign-up:** v0 uses a human-maintained (dummy, synthetic for now) contractor CSV. A live sign-up flow is a later option.
- **Raw job store:** add append-only `data/raw/*.jsonl` in phase 3 (section 8a).
- **Dependency policy:** versions are chosen for the client Mac, not the dev PC. The app runs in a Linux x86_64 container, so the Intel-macOS wheel limits (section 3a) do not apply inside it; what matters is the CPU instruction set (the client's i5 has AVX2, no AVX-512) and Docker Desktop support for the client's macOS version (to verify). `uv.lock` is resolved with `exclude-newer` (cutoff 2026-10-05) so only releases at least about 2 weeks old are used; move the cutoff deliberately and re-test.
- **Locked stack (phase 1):** Python 3.12, torch 2.14.1+cpu (PyTorch CPU index, no CUDA), sentence-transformers 6.1.0, transformers 5.18.0, numpy 2.5.3, pydantic 2.13.5, filelock 4.0.10. Image `jobmatch:dev` is 2.08 GB on disk (456 MB compressed).
- **Embedding model pinned:** `intfloat/multilingual-e5-small` at revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`. Dev-PC smoke test: dim 384, 6.6 ms/text, about 1 GB peak memory. Client-Mac numbers still to measure.
- **Phase 2 (normalization):** `normalize/` turns a `RawJob` into a `Job` with pure, deterministic functions. Language detection is a built-in English/French function-word check (no lingua); words shared with Spanish and other Romance languages are excluded so other languages are not mistaken for French. Skill matching is exact whole-word, accent-insensitive; rapidfuzz (fuzzy dedupe) waits for phase 3. The skill taxonomy v1 covers diverse fields (about 200 skills in 15 categories), since the real contractor list is diverse and will be filled in by hand. New config files: `normalization.yaml`, `canada_locations.yaml`. New dependencies: beautifulsoup4, lxml, ftfy.
- **Hard filters (recall first):** `not_canada` only when a foreign location is explicit, `not_contract` only on a clear permanent signal, `stale` past `normalize.freshness_days`, `unsupported_language` only for texts of 40+ words with no English/French signal. Unknowns stay `new`; postings with no description become `needs_details`.
- **Score-scale finding:** raw E5 cosines are compressed (an unrelated profile scored 0.83 against a job, a related one 0.92), so the GUESS bands in `settings.yaml` (high 0.80, medium 0.65) cannot be used on raw cosine. Phase 4 must rescale scores (for example relative to each job's score distribution) before banding, then tune on reviewer data.

**2026-10-20**
- **Manual intake = posting links.**
  - People add URLs to `data/inbox/links/`, and each pasted link counts as approval to fetch it (rules 4 and 6, as edited).
  - Fetching is a polite plain GET: one at a time with a 2 s pause, robots.txt respected, no cookies, logins or JavaScript; Indeed is blocked.
  - Pages are cached in `data/raw/`, so rebuilds re-normalize without fetching.
  - Paste files stay as a fallback.
- **Normalization v2 splits each posting into labeled parts.**
  - Section kinds are recognized by exact headers or short leading-word patterns; employer pitch, benefits, EEO and application text are excluded.
  - New `Job` fields: `responsibilities`, `nice_to_have`, `years_experience_min`, `commitment` (jobs schema v3).
  - `description_clean` is composed as name → responsibilities → requirements → pay.
  - Skill wording variants are left to embeddings (phase 4), not the taxonomy.
- **Ending lists without over-reading.** In bulleted lists, the first plain sentence after the bullets ends the list; plain-text lists are bounded by headers and caps (20 each). Years of experience is read from all requirement lines, so a trailing "requires seven years" still counts.
- **Rules added:**
  - Work mode with precedence (hybrid + remote → remote; hybrid + on-site → on-site; remote + on-site → first seen).
  - Commitment full-/part-time, never a filter.
  - FTC = fixed-term contract.
  - Number words (EN/FR) for experience and contract length.
  - Pay parsing with unit sanity check; a single amount is stored as `pay_min` only.
- **Freshness:** `freshness_days` was set to 999 by the user, so the stale filter is effectively off for now.

**2026-10-21**
- **Fuzzy dedupe (rapidfuzz):** a posting is a duplicate when company, city and seniority match and the titles are nearly identical after expanding abbreviations (`title_abbreviations` in `normalization.yaml`, `fuzzy_title_threshold: 92` in `settings.yaml`). The duplicate is kept with status `duplicate`.
- **Dead links:** HTTP 404/410 are dropped and remembered in `data/raw/` (kind `gone`), not retried unless `--refresh`.
- **Page encoding:** header charset, then `<meta charset>`, then UTF-8 with a Windows-1252 fallback.
- **Empty inbox files** are skipped, not errors.
- **Deferred:** an "expired" filter from `validThrough`; freshness stays 999 (section 12 reminder).

**2026-10-22**
- **Contractor list:** `build-contractors` builds `data/contractors/contractors.csv` from the master workbook (Talent + Talent Profiles joined on Talent ID; `Contract? = Yes` only; hourly rate, range averaged, else "Industry Standard"). 42 contractors, 15 without a profile. It is read directly for embedding; mapping it to the `Contractor`/facet schema of section 7 is still open.
- **Embedding model changed to `BAAI/bge-m3`** (user decision), revision `5617a9f61b028005a4858fdac845db406aefb181`: 1024 dimensions, no prefixes, cut at 512 tokens (GUESS, keeps CPU time down), batch 8 (GUESS). About 4.5 GB in the model cache and roughly 2.5–3 GB of memory while loaded (estimate, within the 4 GB cap). Dev PC: 42 contractors in 11.4 s (270 ms each); expect several times slower on the client Mac, still to measure.
- **One vector per contractor**, not per facet, for now. The embedding text uses shared labels in a fixed order (Title, Target titles, Seniority, Skills, Industries, then the job's lists or the contractor's summary and proof points); names, location, pay, hours, work mode and company are left out. Details in `DEVELOPMENT.md`; the template replaces the one sketched in section 8.
- **First look at scores (2 jobs × 42 contractors):** top 5 were sensible creative/content/project profiles; best 0.68–0.79, median 0.54–0.61, lowest 0.43–0.48. Wider spread than E5, but the top 5 sit within about 0.05 of each other, so skill and rule scoring still has to separate them. Too small a sample to set thresholds.

**2026-10-26**
- **Matcher built** (`match`, `export-matches`).
  - Score = weighted average of full-text cosine, title cosine and skill overlap, each stretched to 0–1. Weights 0.45 / 0.30 / 0.25 (GUESSES).
  - One fixed scale for all jobs, so a job nobody fits still looks weak.
  - Job-centric shortlist of up to 5, with `min_score` 0.30 (GUESS).
- **User decisions:**
  - **Seniority:** flag under- and over-qualified, with both levels in the reason.
  - **Location:** treat all contract work as remote for now (`assume_all_remote: true`). The province check is built but off, and the user will revisit the logic.
  - **Failed checks:** go to a separate `matches_flagged.csv` for review instead of being dropped.
  - **Rate is never a filter.** A strict rate filter would make it too hard to match jobs to contractors. "Rate above posted pay" stays an information flag.
  - **Commitment and hours:** never filter (unchanged).
- **Must-have skills:**
  - Requirements lines with soft wording ("familiarity", "an asset", "un atout"…) no longer count.
  - Missing must-haves are shown but no longer lower the band (`missing_must_have_lowers_band: false`). With free-text contractor profiles, nearly every contractor missed some, and bands stopped separating anyone.
- **Title similarity added.**
  - Titles are cleaned of junior, intermediate and senior words, and job titles also lose bracketed text.
  - Each contractor's closest title counts.
  - Thin profiles (no summary or strengths) are scored on title and skills only.
- **Skill overlap** = shared ÷ min(job skill count, 4) (GUESS). Long tag lists made one shared skill worth almost nothing.
- **Taxonomy:** 15 media, content and production skills added (editorial planning, journalism, copy editing, digital production, video production, motion graphics, CMS…). Bare "producer", "editor", "analytics" and "dam" are left out as ambiguous.
- **Results on 3 jobs × 42 contractors:**
  - The social media and campaign producer shortlists look right. The CBC editorial role has no strong match, which is correct: nobody shows journalism or CMS experience.
  - Every contractor with a stated rate is 2–4× the posted pay. These postings are salaried fixed-term roles.
  - The Integrated Producer (thin profile) still misses: bge-m3 rates "campaign producer" closer to "creative director" than to "integrated producer". A shared role-word bonus is an option to test once more postings are in.
  - Weights are to be tuned on more postings and reviewer decisions.
- **Open:**
  - how new skills enter the taxonomy (suggested: a local `suggest-skills` report plus human approval; an LLM only later, posting text only, behind a disabled-by-default provider);
  - the link-clearing commit must not break `reset_reimport.sh`, which rebuilds only from the inbox.
- **Human-reviewed matches imported as labels** (`import-reviewed-matches`, `evaluate-matches`).
  - The sheet has 85 filled-in rows; 33 are contract-type and become labels.
  - The 11 active contract postings among them were added to the links file. All 11 failed to import: 4 removed (404), 4 blocked (HTTP 403: jobs.ca, CareerBeacon), 1 LinkedIn page disallowed by robots.txt, 2 Workday pages that need JavaScript.
  - So only 1 reviewed pair is currently comparable. Talents TAL-BC-005 and TAL-BC-008 appear in the reviews but not in the contractor list (not marked "Contract? = Yes").
- **What the reviewers' scoring shows** (all 85 rows, permanent ones included, used for insight only):
  - The reviewers' skills sub-score barely separates the tiers: 21–30 everywhere, including rejects. Tiers are decided by contract likelihood (20 contract, 12 conversion, 8 permanent), availability and location.
  - Rejects are about logistics, not skills. Of 17: 5 Ontario outreach blocked until recruiter licensing is confirmed, 4 relocation or scope, 3 talent unavailable until a date, 2 posting gone, 2 level mismatch, 1 full-time vs part-time, 1 foreign employer.
  - Location is a business rule: Ontario hybrid/on-site roles score 4/15 for licensing reasons, remote roles 15/15. This is evidence for when `assume_all_remote` is revisited.
  - Availability is scored, not filtered, including "unavailable until <date>". We have hours but no availability date.
  - Canadian-employer eligibility is its own gate (US/EU employers with remote postings were excluded), and our location filter doesn't catch it.
  - Rate gaps are noted as risks and never reject, which matches the team decision. Level mismatch is noted but rarely rejected, so our seniority flag may be stricter than reviewers. Industry fit shows up in gaps.
- **Candidate improvements, not built:**
  - an Ontario hybrid/on-site flag;
  - an availability score (hours plus an availability date);
  - a Canadian-employer check from the posting's hiring organization or applicant location;
  - a job industry field;
  - Workday's public job JSON for Workday postings (a new feed, needs approval).
- **Skill taxonomy widened from the contractor list** (user request).
  - About 60 curated skills were added (marketing and go-to-market, sales, design and UX research, operations and leadership, HR, finance and funding, technology leadership), with English and French aliases and synonyms merged. Generic single words are kept out (ai, design, operations, writing…).
  - Contractor strength phrases recognized: from about 28% to 80% (118 of 143 after a few synonym aliases).
  - `build-contractors` now learns the remaining phrases automatically into `data/contractors/skills_learned.yaml`, a generated layer loaded with the curated file (curated wins). Merge hints use a token-sort ratio; a token-set ratio gave false pairs such as "social strategy" with "hr strategy".
- **Job titles:** the site suffix after " | " and "Expired" markers are removed (config patterns).
- **Effect on the reviewed Ross Video pair** (TAL-BC-006, reviewers A/90): our rank went from #10 to #3, score 0.277 to 0.532, now shortlisted. Still only 2 comparable pairs.
- **Type of work and industry** (user request, following the user's judgment that the Ross Video ranking with a marketing director first was right and a sales director at #2 was not).
  - **Type of work** is a light score part (`weights.field` 0.10, GUESS): the share of the job's skill categories the contractor covers, ignoring `management`, `business` and learned skills. A note appears when the job's main type of work isn't the contractor's.
  - **Industry** is a note only, never scored, from `config/industries.yaml` (about 22 sectors, English and French). Reviewers rated a cleantech B2B marketer A for a sports-industry role, so industry must not filter.
  - No schema change: the field part is folded into the score and both notes go in `flags`.
  - **Ross Video:** the sales and business development director dropped from #2 to #4 (note "field: job marketing · contractor admin, sales"); the reviewers' A pick rose to #2.

