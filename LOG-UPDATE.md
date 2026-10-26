# Log update

What each change solves, for the team. Newest first. Add an entry with every commit.

---

## 2026-10-22 · Vectors for jobs and contractors (phase 4, first part)

### What this solves
Matching needs a way to tell how close a job is to each contractor, even when they use different words ("chef de projet" vs "project manager", "campaign producer" vs "content strategist"). Each job and each contractor is now turned into one **vector**, a list of numbers that captures what the text means. Similar vectors mean similar work. The scorer (next step) will combine this with skills and rules; on its own it only ranks.

### How to use it
After importing jobs or rebuilding the contractor list, run:
```
docker compose run --rm app python -m jobmatch embed-contractors
docker compose run --rm app python -m jobmatch embed-jobs
```
Each prints counts and timing, never names:
```
contractors: 42 vector(s) · 42 embedded · 0 unchanged · 0 dropped
  model BAAI/bge-m3 (5617a9f…+sentence-transformers-6.1.0) · 1024 dimensions · 11.36 s (270 ms/text)
```
Re-running is cheap: only new or edited rows are embedded again, and removed rows are dropped.

**First-time setup on a new machine:** the model (about 4.5 GB) must be downloaded once, with network allowed for that one run:
```
docker compose run --rm -e HF_HUB_OFFLINE=0 -e TRANSFORMERS_OFFLINE=0 app python -m jobmatch smoke-embed
```

### What goes into each vector
Both sides are written as the same labeled lines, in the same order, so the model compares like with like:

| Line | Contractor | Job |
|---|---|---|
| Title | headline | job title |
| Target titles | target titles, Primary Role first | — |
| Seniority | seniority | seniority |
| Skills | skills found in the profile, then core strengths | skills found in the posting |
| Industries | main industries | — |
| Responsibilities, Requirements, Nice to have | — | the posting's lists |
| Summary, Proof points | executive summary, proof points | — |

**Left out on purpose:** names, IDs, location, pay or rate, hours, work mode and company. These are yes/no checks in matching, not what the work is about, and names are personal data. To see exactly what was embedded, open `data/embeddings/contractors_texts.jsonl` or `jobs_texts.jsonl`.

### First results (2 jobs, 42 contractors)
For both producer jobs, the 5 closest contractors were content, creative, design and digital project people, which is sensible. Scores ran from about 0.45 (least related) to 0.79 (closest). The top 5 were close together, so the vectors can't pick the winner on their own; that's the scorer's job. Two jobs are too few to set any thresholds.

### Known limits
- **The model changed:** it's `BAAI/bge-m3` now, not e5-small. It's bigger: about 4.5 GB on disk and roughly 2.5–3 GB of memory while running (an estimate). Speed on the client Mac is still to measure; expect several times slower than the 270 ms per contractor seen on the dev PC.
- **Long texts are cut:** after about 512 tokens (roughly 350 words), the tail of a long summary or posting isn't used. Titles and skills come first, so they're never cut.
- **Thin profiles:** the 15 contractors without a profile have only a title and target titles, so their vectors are vaguer.
- **One vector per contractor:** someone with two different specialties gets one blended vector. Separate vectors per specialty are a later option.

### Changed in this commit
- **New commands:** `embed-contractors`, `embed-jobs`.
- **New code:**
  - `embed/texts.py`: the text layout
  - `embed/build.py`: incremental embedding
- **Config:** `settings.yaml`:
  - model `BAAI/bge-m3` with its pinned revision, no prefixes, batch 8, 512 tokens
  - `paths.contractors_csv`
- **Schemas:** the contractor row model moved to `schemas.py` (`ContractorProfile`), with a read-only repository.
- **Docs:** `DEVELOPMENT.md` (Embeddings section), `PROJECT_CONTEXT.md` (decisions log).

### Checks run
- 413 automated tests pass, with a fake model; lint and format are clean.
- The real model loads offline from the pinned revision, and the English/French smoke check passes.
- A real run embedded 42 contractors and 2 jobs.

---

## 2026-10-22 · Contractor list built from the master workbook

### What this solves
Matching needs one clean list of the contractors we represent. This builds it straight from `contractors_master_list.xlsx`, joining the **Talent** and **Talent Profiles** tabs on Talent ID. Only people marked as open to contract work are included. Re-run it whenever the workbook changes.

### How to use it
1. Save the updated workbook as `data/contractors/contractors_master_list.xlsx`.
2. Run:
   ```
   docker compose run --rm app python -m jobmatch build-contractors data/contractors/contractors_master_list.xlsx
   ```
3. The result is `data/contractors/contractors.csv`, which opens cleanly in Excel. The previous version is first copied to `data/contractors/backups/`. The command prints counts only, never names:
   ```
   Wrote 42 contractor(s) to data/contractors/contractors.csv
     with a profile: 27 · without a profile: 15
     excluded: 2 not confirmed as contract · 6 profile(s) with no Talent row
     rate: 15 hourly figure(s) · 27 set to the default
   ```

### What's in the file
| Column | Comes from |
|---|---|
| `talent_id`, `talent_name` | Talent ID, Talent Name |
| `location` | Profile location; if it says "Location to confirm", the City, Province from the Talent tab |
| `standardized_headline` | Profile headline; if the person has no profile, their Primary Role |
| `executive_summary`, `seniority` | Profile, as written |
| `core_strengths`, `proof_points`, `main_industry` | Profile lists, separated by `\|`. Industries are the whole list, main one first |
| `weekly_hours` | Weekly Capacity (Talent tab). Full-time or part-time can be read from this |
| `rate_expectation` | Hourly rate from Rate Expectation. A range is averaged ($45–$60 → 52.5), "$150+" gives 150, and anything without a clear figure is `Industry Standard` |
| `target_titles` | Primary Role first, then Target Titles 1–5, duplicates removed |
| `contract` | always `true` |

**Who's included:** people whose `Contract?` is "Yes". "No", blank, and profiles without a Talent row are left out. Engagement preference is not exported.

### Known limits
- **Thin rows:** 15 contractors have no profile yet. Their rows hold only ID, name, location, headline, hours, rate and titles, so they'll match poorly until their profiles are filled in.
- **Zero hours:** 2 contractors have a Weekly Capacity of 0, and 11 have none.
- **Hourly only:** rates are read as hourly. A yearly figure becomes `Industry Standard`.
- **Not connected yet:** matching (phase 4) still has to be wired to this file.

### Changed in this commit
- **New command:** `build-contractors`.
- **New code:** `src/jobmatch/sources/xlsx.py` (reads Excel files with no extra packages) and `src/jobmatch/sources/contractor_sheet.py`.
- **New config:** `config/contractor_import.yaml`, with the tab and column names and the rules. Edit it if the workbook's columns are renamed.
- **CSV output:** whole numbers in exported CSVs now read `40`, not `40.0`.

### Checks run
- 388 automated tests pass, 17 of them new. They use a made-up workbook, with no real names.
- Lint is clean.
- A real run on the workbook gave 42 contractors with unique IDs; every row has a location, headline, rate and target titles.

---

## 2026-10-21 · Fewer duplicates, dead links dropped, accents fixed (phase 3, manual intake)

### What this solves
- **The same job posted twice with a slightly different title is caught.** "Sr. Data Engineer" and "Senior Data Engineer" at the same company and city are now recognized as one job. The second is stored as `duplicate` with a pointer to the first, so you only review it once.
- **Dead links no longer fail every run.** A posting that has been taken down (the site answers "404 Not Found" or "410 Gone") is dropped: it shows as `skipped · page gone`, is remembered, and isn't fetched again. Before, it was retried and reported as an `ERROR` on every run.
- **Accents on older French sites come out right.** Pages that don't declare their character set in the usual place are now read correctly, so "Montréal", not "MontrÃ©al".
- **Empty files in the inbox are skipped quietly** instead of reported as errors.

### How to use it
Nothing new to learn. Add links and run the same script as before:
```
bash scripts/fetch_and_import.sh            # import new links (and paste files)
bash scripts/fetch_and_import.sh --refresh  # fetch everything again, including links marked "page gone"
```
New lines you may see in the report:
```
  skipped     links.txt:5   page gone (HTTP 404): dropped, not fetched again
  skipped     links.txt:5   page gone (HTTP 404) since 2026-10-20; not fetched again (--refresh retries)
  duplicate   links.txt:9   manual:url-… · same job as manual:url-… (similar title)
  skipped     example.txt   empty file
```
You can leave dead links in your file, or delete them; either way they cost nothing.

### What counts as a duplicate
All of these must be true:
- the same company
- the same city
- the same seniority level
- titles that are nearly identical after expanding common abbreviations (Sr. → Senior, Mgr → Manager, Eng. → Engineer, & → and…)

So "Data Analyst" and "Senior Data Analyst" stay separate. The abbreviation list is in `config/normalization.yaml` (`title_abbreviations`), and the strictness is `fuzzy_title_threshold: 92` in `config/settings.yaml`.

### Known limits
- **Openings that look identical are merged:** two genuinely separate openings at one company, in one city, with near-identical titles become one. The second is kept as `duplicate`, so it's still visible.
- **Reminder, fix before real use:** the freshness limit is still 999 days (old postings aren't filtered) so the matching algorithm can be tested. There's also no check yet for postings whose closing date has passed.

### Changed in this commit
- **New dependency:** `rapidfuzz`, about 3 MB.
- **Code:**
  - `normalize/dedupe.py`: the similar-title rule
  - `sources/web.py`: page encoding and dead-link status
  - `ingest.py`: dead-link memory, empty files
- **Config:** `title_abbreviations` in `normalization.yaml`; `fuzzy_title_threshold`, plus the freshness reminder, in `settings.yaml`.
- **Docs:** `README.md` (collecting jobs is now by link), `DEVELOPMENT.md`, `PROJECT_CONTEXT.md`, this file.

### Checks run
- 405 automated tests pass; lint is clean. (These changes were lost from disk once on 2026-10-21 and restored on 2026-10-22; the tests were re-run after the restore.)
- A real run dropped one dead CharityVillage link and kept the 2 live jobs.

---

## 2026-10-20 · Import jobs from posting links (phase 3, manual intake)

### What this solves
You no longer copy postings into files by hand. You paste the **link** to a job posting, and jobmatch fetches the page, reads the job details and adds a cleaned record to `data/jobs.csv`, ready for matching in phase 4. This works for most job boards and company career pages, and for LinkedIn links you paste yourself.

### How to add jobs
1. Open any `.txt` file in **`data/inbox/links/`**, or create one, e.g. `data/inbox/links/2026-10-21.txt`. `data/` is not in git, so on a fresh checkout create the folder first: `mkdir -p data/inbox/links`.
2. Put **one posting URL per line**:
   ```
   # Lines starting with # are notes and are ignored.
   https://jobera.com/job/dept-senior-campaign-producer-6-month-ftc-c190dd5a/
   https://builtin.com/job/senior-producer-cbc-life-english-services-hybrid/10651869
   https://www.linkedin.com/jobs/view/4012345678/   # anything after the link and a space is ignored
   ```
3. Save the file. Keep the links in the file afterwards: links already imported are skipped, so you can keep adding to the same file.

**Indeed links are never fetched** (project rule). For those, or for a page that won't load, paste the posting as text into a file in `data/inbox/manual/`. Copy `data/samples/manual/_TEMPLATE.txt`, fill in `url` and `title`, then paste the text under the `---` line.

### How to run it
From the project folder (Docker must be running):

| Command | What it does |
|---|---|
| `bash scripts/fetch_and_import.sh --dry-run` | Preview: fetches the new links and shows what would be imported. Writes nothing. |
| `bash scripts/fetch_and_import.sh` | Imports new links, then any paste files in `data/inbox/manual/`. |
| `bash scripts/fetch_and_import.sh --links-only` | Imports links only. |
| `bash scripts/fetch_and_import.sh --refresh` | Fetches every link again, even ones imported before. |
| `bash scripts/reset_reimport.sh` | Rebuilds `jobs.csv` from scratch (asks first). Old files move to `data/backups/reset-<time>/`, nothing is deleted. Pages already fetched are reused, so it needs no internet. Add `--refetch` to fetch everything again. |

The script prints one line per link:
```
Pages fetched from the network: 3
  ERROR           2026-10-examples.txt:5     fetch: HTTP 404            ← the posting was taken down
  new             2026-10-examples.txt:6     manual:url-c0f151e941a77324
  new             2026-10-examples.txt:7     manual:url-9444c1afe55df9be
  skipped         2026-10-examples.txt:8     ca.indeed.com is never fetched automatically; paste the posting by hand
  already stored  2026-10-examples.txt:9     manual:url-…               ← imported in an earlier run
  filtered out    2026-10-examples.txt:10    manual:url-… · not_contract ← stored, but not a contract role
```
- `filtered out` jobs are kept, with the reason; nothing is silently dropped.
- `ERROR` lines don't stop the run. Fix or remove the link and run again.

### What jobmatch reads from each posting
- **Basics:** title, company, city/province, work mode (on-site / hybrid / remote) and language (en / fr / bilingual).
- **Contract terms:**
  - contract vs permanent, from phrases like "contract", "fixed-term", "FTC", "CDD" or "mandat"
  - contract length ("6-month", "one-year contract", "contrat d'un an")
  - full-time / part-time
- **The job itself:** responsibilities, requirements, nice-to-haves, years of experience, skills and seniority.
- **Pay:** range, unit and currency. A single amount is shown as one number, not a range.
- **Clean description:** a short version for matching and review, in the order position → responsibilities → requirements → pay. Company pitch, benefits, equal-opportunity text and "how to apply" lines are left out.

### Changed in this commit
- **New:**
  - `ingest-links` command
  - `scripts/fetch_and_import.sh`
  - `config/sources.yaml` (fetch settings and blocked sites)
  - `src/jobmatch/sources/` links, web fetcher and page reader
- **Normalization:**
  - postings split into labeled sections
  - new fields `responsibilities`, `nice_to_have`, `years_experience_min` and `commitment` (jobs schema v3)
  - lists end cleanly, without absorbing salary or "apply" lines
  - EN/FR number words; work-mode precedence; FTC as fixed-term contract
- **`scripts/reset_reimport.sh`** now keeps the page cache by default.
- **Docs:** `DEVELOPMENT.md`, `PROJECT_CONTEXT.md` (decisions log), and this file.

### Known limits
- **Unreadable pages:** sites that need a login or JavaScript, or that block automated requests (LinkedIn often does), show as `ERROR`. Paste those by hand.
- **Failed links are retried:** a failed link is tried again on every run, so put `#` in front of dead links.
- **Plain-text lists:** lists without bullet markers are bounded only by section headers and the 20-item cap.
- **Hand-written rules:** skill, city and phrase lists are hand-written and will have gaps. Report misses, and include the posting link.
- **Not tested on the client's Mac yet.**

### Checks run
- 371 automated tests pass. They never use the internet; fetching is simulated.
- Lint is clean, and the Docker image was rebuilt from the updated lockfile.
- A live run on 3 real links: 2 imported correctly; 1 posting had been removed (HTTP 404).
