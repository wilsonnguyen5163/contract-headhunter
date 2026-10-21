# Log update

What each change solves, for the team. Newest first. Add an entry with every commit.

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
