# Log update

What each change solves, for the team. Newest first. Add an entry with every commit.

---

## 2026-10-28 · Trial-run export in the pipeline, quick start guide and new README

### What this solves
- **The notebook's input is now made by a command.** `data_visualizer.ipynb` reads `exports/trial-run-<time>.csv`. Until now, that file was written by hand outside the code. `export-trial-run` now writes it from the latest matching run:
  - the best shortlisted contractors per active job (2 by default; flagged matches are left out);
  - one "no match" row for a job with no shortlist;
  - the same columns as before, so the notebook needs no change.
- **[quick_start.md](quick_start.md):** every command in one place, plus the steps to import jobs, reset and re-import, run the model, find the results and read them in the notebook.
- **README.md** is now a standard project README: what the repo does, how it works, setup, usage, structure and a map of the docs.

### How to use it
- After `match`, run `docker compose run --rm app python -m jobmatch export-trial-run`. Then open `data_visualizer.ipynb` and run `view_trial_run()`.
- To change the number of contractors per job, set `exports.trial_run_per_job` in `config/settings.yaml`. It sits outside `matching:` on purpose, so changing it doesn't change the scoring version or add match rows.

### Known limits
- The file holds contractor names: keep it local.
- The notebook still uses absolute paths (`/home/wilson/...`). Change them on another machine.

### Checks run
- New tests in `tests/test_match_run.py`: best-per-job selection, flagged matches left out, the "no match" row, the `per_job` limit and the column order. (Not run yet: waiting for approval.)

---

## 2026-10-27 · Cleaner skill tags, a stricter shortlist and a first labeled check on 33 new jobs

### What this solves
- **Fewer wrong skill tags.**
  - New `not_when` option in `config/skills_taxonomy.yaml`: phrases that contain a skill word but mean something else. "you excel at…" is no longer Excel. Hiring-process text ("an accessible recruitment experience") is no longer the recruitment skill. French "animation d'ateliers" is not animation.
  - Removed aliases that tagged the wrong jobs: bare "ui", "figma" and "prototyping" (no longer UI/UX), "continuous improvement" (now process improvement, not Six Sigma), "trainer", bare "purchasing", and "mergers and acquisitions" (now a finance skill, not corporate law).
- **Missing skills added:**
  - "graphic designer" and "graphiste" (these postings had no tags before);
  - event coordination;
  - animation;
  - Webflow (as a CMS);
  - PeopleSoft and Workday Financials (as ERP);
  - email design;
  - industrial design;
  - Figma, as a tool with no type of work.
- **Twine job titles:** "I8IS INC. - Senior Full Stack Developer freelance job | Twine" was cut to "i8is inc."; it now reads "senior full stack developer".
- **Job text no longer lost under "About the …" headers:** "About the Opportunity" and "About the Requirements" were read as the employer's "About us" pitch. All the duties and requirements after them were dropped (Tucows Graphic Designer, Copywriter). They are now job headers in `config/normalization.yaml`, along with "Job Duties" and "Qualifications Required".
- **Stricter shortlist:** `min_score` went from 0.30 to 0.40. Jobs with no real fit in our pool (payroll, tax, data engineering, animation…) now more often show no shortlist instead of five weak names.
- **First labeled check on new jobs:** `data/eval/llm_judge_labels.csv` holds fit / partial / no labels for each of the 33 new jobs' shortlists, plus fits the matcher missed.

### How to use it
- After pulling, run `bash scripts/reset_reimport.sh -y`, `embed-contractors`, `embed-jobs` and `match`. This re-tags the jobs that are already stored.
- To exclude a phrase from a skill, add it under that skill's `not_when` in `config/skills_taxonomy.yaml`.
- `scripts/rename_manual_templates.sh [--dry-run]` renames filled-in `_TEMPLATE copy*.txt` files to their title. It's a local helper and is git-ignored.

### Known limits
- **The labels are Claude's, not a reviewer's,** and they are not blind. Treat the 0.40 threshold as a guess until reviewers label more pairs.
- **Thin contractor profiles get missed.** TAL-ON-023, -027, -028, -029 and -030 have no strengths or industries on file. They were the fits most often left out of the top 5.
- **Some pages can't be read:** Zoho Recruit, BambooHR and similar pages need JavaScript, and macommunaute.ca only gives a short teaser. Paste such postings into `data/inbox/manual/`. The 5 Zoho postings are now pasted; the BambooHR link is commented out until someone pastes it.
- **Animation job vs art directors:** with the wrong recruitment tag gone, the Senior Animation Artist posting counts as media work, like the art directors. Five art directors now reach the medium band. Each one shows "missing must-have: animation".
- **"technical support" and "GTM" as department names** still tag skills.

### Checks run
- **Tests:** 594 automated tests pass (new English and French cases for every rule above); lint is clean.
- **Real data:** 48 jobs × 42 contractors. Against the labels:
  - high band 91% fit;
  - medium 38% fit and 44% partial;
  - low 10% fit.
  Shortlist precision went from 0.33 to 0.47 with the new threshold.
- **After the reset re-tagged stored jobs (2026-10-28), header fix included:** 80 listed pairs, precision 0.45 (0.59 counting half of partials). High band 90% fit, medium 38% fit, low 6% fit. 10 fits missed. 602 tests pass.

---

## 2026-10-26 · One-line postings get their sections back; over-qualified is a note

### What this solves
- **One-line postings are now split up.** Some sources, including every Workday posting (Clio, BCAA) and Safe Software's page, deliver the posting as one long run of text, with headings and list items running together. jobmatch now puts known headings on their own line, splits responsibilities and requirements into separate items, and moves the pay sentence out of the benefits text. Those postings now have real responsibilities and requirements, cleaner skills, and correct pay. Clio was showing a $2,000 benefit as the minimum salary; it now shows $110,300–149,300.
- **Over-qualified no longer moves someone to the flagged list.** Our reviewers note a level mismatch but rarely reject for it, so it's now a note on the match. Under-qualified still moves someone to the flagged list.
- **Type of work counts more:** its weight went from 0.10 to 0.20, so people from a different type of work rank lower.

### How to use it
Nothing new to run. After pulling, run `bash scripts/reset_reimport.sh -y`, `embed-jobs` and `match`. The rules are in `config/normalization.yaml` under `flattened_text`. That section holds:
- when text counts as flattened;
- which words start a new list item;
- which "-ing" words are nouns, not new items.

### Known limits
- **It's still keyword matching:** words used in a different sense are tagged as skills. Examples: "GTM leaders" (a department), "you excel at" (not the spreadsheet), "positioning yourself".
- **Type of work at 0.20** also pushes designers up for producer jobs whose skill tags are mostly design. The weight is a first guess to tune on more reviews.

### Checks run
- **Tests:** 568 automated tests pass, English and French; lint is clean.
- **Real data:** 12 jobs re-imported, re-embedded, matched and evaluated. The reviewers' A/B pick is still shortlisted on 4 of 5 jobs.

---

## 2026-10-26 · Fixes found while checking against reviewed jobs

### What this solves
Running the matcher on the reviewed postings you saved (Create Music ×2, Safe Software, Maneva) showed six problems, now fixed:
- **Postings whose headings run into the paragraphs** (Safe Software) lost almost all their text and skills. Now, if no responsibilities or requirements are found, the whole job text is used for skills and the description.
- **New skills:** webinars and virtual events (Livestorm, GoToWebinar, ON24), and marketing qualified leads (MQL).
- **Industry note:** "university degree" or "minimum education" in requirements no longer tags a job as the education industry.
- **LinkedIn pages saved while signed in** had no web address; the posting number in the page is now used.
- **Paste files are read before saved pages,** so when the same posting is both pasted and saved, the hand-made paste wins. A signed-in LinkedIn page mostly holds the job list, not the posting.
- **Stale rows:** after a change to skills, industries, rules or a posting or profile, `match` kept the old rows. It now stores fresh ones, and the export shows only each job's latest run.

### How to use it
Nothing new to run. After pulling, run `bash scripts/reset_reimport.sh -y`, then `embed-jobs`, `match` and `export-matches`.

### Known limits
- **Unsplit headings:** for postings like Safe Software, the whole-text fallback also picks up a few unrelated skills from the surrounding text (logistics, help desk, privacy). Properly splitting inline headings is still to do.

### Checks run
- **Tests:** 560 automated tests pass; lint is clean.
- **Real data:** 8 jobs re-imported, re-embedded, matched and evaluated. The reviewers' A/B pick is in our shortlist on 4 of 5 jobs (the miss is Maneva).

---

## 2026-10-26 · Type of work and industry in matching

### What this solves
A sales director could rank near the top for a marketing job because their wording looked similar. Now the matcher:
- **Compares the type of work** (marketing, sales, software, finance…, taken from each side's skills) and gives it a small weight in the score. When they differ, the match carries a note such as `field: job marketing · contractor sales`.
- **Notes the industry** on both sides, for example `same industry: media and entertainment`, or `industry: job sports · contractor cleantech`. This is information only: our reviewers have rated people from other industries as strong fits, so industry never lowers a score or removes anyone.

On the Ross Video marketing role, the sales and business development director moved from 2nd to 4th, and the person our reviewers rated A moved up to 2nd.

### How to use it
Nothing new to run. `match` and `export-matches` show the notes in the flags.
- **Weight:** `weights.field` in `config/settings.yaml` (0.10, a first guess).
- **Ignored skill types:** `field_ignore_categories`, for skills used everywhere, such as project management.
- **Industry names and spellings:** `config/industries.yaml`.

### Known limits
- **Type of work needs categorized skills on both sides:** contractors without listed strengths get no field part.
- **Industry is read from words in the posting:** a SaaS company hiring for a healthcare client may show both. That's why it's a note only.

### Changed in this commit
- **New code:** `match/fields.py`; the scorer and run pass type of work and industry.
- **Config:** `config/industries.yaml`; `weights.field` and `field_ignore_categories` in `settings.yaml`.

### Checks run
- **Tests:** 551 automated tests pass, 10 of them new; lint is clean.
- **Real data:** dry run on 4 jobs × 42 contractors; the evaluation still has the reviewers' A pick shortlisted, now at #2.

---

## 2026-10-26 · Wider skill list, learned from our contractors

### What this solves
Skill matching only works when the same skill is recognized in both the job and the contractor profile. Before this change, only about a quarter of our contractors' listed strengths were recognized. Marketing, go-to-market, sales and operations were mostly missing. Now:
- **About 60 skills were added by hand**, in English and French, with synonyms grouped. "GTM strategy", "go-to-market" and "mise en marché" count as the same skill.
- **New strengths are learned automatically** when the contractor list is rebuilt: any strength the list doesn't recognize yet is added on its own, so jobs that mention it are tagged too.
- **Job titles are cleaner:** job-board suffixes like "- Expired | Ross Video" are removed before titles are compared.

On the reviewed Ross Video posting, the contractor our reviewers rated A moved from 10th to 3rd and now makes the shortlist.

### How to use it
After updating the contractor workbook:
```
docker compose run --rm app python -m jobmatch build-contractors data/contractors/contractors_master_list.xlsx
bash scripts/reset_reimport.sh -y
docker compose run --rm app python -m jobmatch embed-contractors
docker compose run --rm app python -m jobmatch embed-jobs
docker compose run --rm app python -m jobmatch match
```
`build-contractors` now also prints how many strengths were already known, learned, or skipped as too generic:
```
Skills: 143 strength phrase(s) · 118 already known · 13 learned · 12 too short or generic
```

### Curating (optional, any time)
- **Learned skills:** `data/contractors/skills_learned.yaml`. They're rebuilt every time, so don't edit this file.
- **Merging synonyms:** to group a learned phrase with its synonyms, add it as an alias in `config/skills_taxonomy.yaml`. For example, "social strategy" is now an alias of social media. It then drops out of the learned file automatically.
- **Hints:** `data/contractors/skill_suggestions.csv` lists near-spellings of existing skills. It catches spelling variants, not meaning, so a person still decides synonyms.
- **What's never learned:** single generic words (design, operations, writing, AI…), because they would tag unrelated jobs. The list is in `config/skill_learning.yaml`.

### Known limits
- **Unmerged synonyms:** learned skills aren't grouped with their synonyms until someone curates them.
- **Re-import needed:** new skills reach jobs only after the re-import step above.
- **Small sample:** only 2 reviewed pairs can be compared so far, so the improvement isn't measured statistically yet.

### Changed in this commit
- **Config:**
  - `config/skills_taxonomy.yaml`: about 60 skills plus aliases;
  - new `config/skill_learning.yaml`;
  - `paths.learned_skills` in `settings.yaml`;
  - title patterns in `normalization.yaml`.
- **Code:** new `normalize/skill_learning.py`; the taxonomy loader merges learned skills; `build-contractors` learns skills.
- **Data:** `data/contractors/skills_learned.yaml` and `skill_suggestions.csv`, generated.

### Checks run
- **Tests:** 541 automated tests pass, 30 of them new, in English and French. They include "j'ai" never counting as "AI", and generic words tagging nothing.
- **Real run:** contractor list rebuilt; jobs re-imported offline (7 unreadable links still fail as expected); both sides re-embedded; evaluation rerun.

---

## 2026-10-26 · Import postings saved as web pages or PDFs

### What this solves
Some job sites refuse automated requests (jobs.ca, CareerBeacon), forbid them (LinkedIn), or only show the posting after JavaScript runs (Workday). For those, you can now save the posting from your browser and drop the file in the inbox. No copy-and-paste into the text template is needed.

### How to use it
1. Open the posting in your browser.
2. Save it:
   - **Best:** "Save page as…" (`.html`). The browser saves the finished page, often with the site's structured job details.
   - **Or:** Print → "Save as PDF", **with headers and footers turned on**. The footer carries the posting's web address, which jobmatch needs.
3. Put the file in `data/inbox/manual/` and run the usual import:
   ```
   bash scripts/fetch_and_import.sh
   ```

The import shows one line per file, like a paste file. If the same posting is later imported by its link, it isn't added twice.

### Known limits
- **PDFs give plain text only:** pay, location and dates are read from the text, so a saved web page is more reliable.
- **Unreadable PDFs:** scanned (image) and password-protected PDFs can't be read, and the import says so.
- **No web address:** a PDF printed with headers and footers off has no address and is reported with a hint.
- **Expired postings are still imported as new.** jobs.ca, for example, keeps expired postings online with an "expired" banner. An expired-posting filter is a separate decision.

### Changed in this commit
- **New code:** `sources/saved_files.py`; manual intake now also reads `.html`, `.htm` and `.pdf`.
- **New dependency:** `pypdf` 6.19.0 (about 1 MB, pure Python); the lockfile and image were rebuilt.

### Checks run
- **Tests:** 511 automated tests pass, 11 of them new (synthetic web pages and PDFs built in the test); lint is clean.
- **Real file:** a saved jobs.ca page (Ross Video, expired) previewed read-only. It gave the right address, contract, 13 months, remote, 5+ years, $82k–120k/year and English. The company and the requirement bullets came out incomplete, and the expired status wasn't caught.

---

## 2026-10-26 · Checking the matcher against human-reviewed matches

### What this solves
We now have a way to measure the matcher against what our reviewers decided, instead of judging it by eye. A reviewed-match sheet (each row: a talent, a posting, the reviewer's score and tier) is imported as labels. The matcher's score and rank for the same pairs are then set beside the reviewer's.

### How to use it
1. Export the reviewed-match sheet as CSV, for example `data/test_data_matches/raw.csv`.
2. Import it:
   ```
   docker compose run --rm app python -m jobmatch import-reviewed-matches data/test_data_matches/raw.csv
   ```
   Only contract-type rows are kept; permanent roles are out of scope. Names, contacts and outreach notes are not imported.
3. After the postings are imported and `match` has run:
   ```
   docker compose run --rm app python -m jobmatch evaluate-matches
   ```
   It prints, and writes to `exports/evaluation-<time>.csv`:
   - how closely our ranking agrees with the reviewers';
   - reviewer tier against our band;
   - for each job, how many of the reviewers' A/B talents made our shortlist;
   - why any pair couldn't be compared.

### Known limits
- **Few comparable pairs today:** 1 of 33 reviewed pairs can be compared. Most reviewed postings have been removed since the review, or block automated reading (jobs.ca, CareerBeacon, LinkedIn, Workday). To include them, paste their text into `data/inbox/manual/`.
- **Different scoring:** the reviewers' score also covers availability, location rules, how likely the role is a contract, posting age and urgency, which the matcher doesn't score. Compare tiers and the skills sub-score rather than the totals.
- **Two reviewed talents** aren't in our contractor list (not marked as open to contract work).

### Changed in this commit
- **New commands:** `import-reviewed-matches`, `evaluate-matches`.
- **New code:** `sources/reviewed_matches.py`, `match/evaluate.py`; the matcher can now score every pair, not just the shortlist (`score_all_jobs`).
- **Config:** `config/reviewed_import.yaml` (sheet column names, tiers, what counts as permanent).
- **Data:**
  - `data/eval/reviewed_matches.csv` (labels);
  - 11 contract posting links added to `data/inbox/links/2026-10-examples.txt` (none could be imported automatically; see above).

### Checks run
- **Tests:** 500 automated tests pass, 6 of them new (synthetic rows, no real names); lint is clean.
- **Real run:** 33 labels imported (52 permanent rows left out); `evaluate-matches` compared the 1 available pair.

---

## 2026-10-26 · Matching: a shortlist of contractors for every job (phase 4)

### What this solves
For each job, jobmatch now picks up to 5 contractors who fit, gives each a score and a confidence level (high, medium or low), and explains why using only our own data: the closest title, the shared skills and the missing ones. People who would fit but fail a basic check, such as a junior for a senior role, are kept in a separate list with the reason, so nobody is silently dropped. The system recommends; a person decides.

### How to use it
After importing jobs, run these in order:
```
docker compose run --rm app python -m jobmatch embed-contractors   # only after the contractor list changes
docker compose run --rm app python -m jobmatch embed-jobs
docker compose run --rm app python -m jobmatch match --dry-run      # look first; stores nothing
docker compose run --rm app python -m jobmatch match
docker compose run --rm app python -m jobmatch export-matches
```
`match` prints each job's shortlist with talent IDs and headlines, never names:
```
senior campaign producer  [manual:url-9444c1afe55df9be]
  1. 0.743 high    TAL-BC-001  Senior Digital & Technical Project Manager
       text 0.769 · title 0.816 (producer) · skills 0.25
       - missing must-have: graphic design, web design, motion graphics, …
  3. 0.618 FLAGGED TAL-BC-042  Project Coordinator, Media Planner & Analytics Specialist
       - seniority: under-qualified (job senior, contractor junior)
```
`export-matches` writes a spreadsheet to `exports/matches-<time>.csv`, with contractor names, job links and every score. **It contains names: keep it on your machine and don't share it outside the team.**

### How a match is scored
Three things are compared, each worth 0 to 1, then averaged with weights:

| Part | Weight | Compares |
|---|---|---|
| Overall meaning | 0.45 | the whole job text vs the whole profile |
| Title | 0.30 | the job title vs the contractor's closest title ("producer" vs their target titles) |
| Skills | 0.25 | how many of the job's main skills the contractor lists |

- **Bands:** high from 0.70, medium from 0.50, low below that.
- **Profiles with only a title** are judged on title and skills, so missing text doesn't count against them.
- **Information only, never filters:**
  - missing must-have skills
  - a rate above the posted pay
  - a French posting
  - a thin profile
  - hours
- **Checks that send a match to the flagged list:**
  - **Seniority:** two or more levels apart, in either direction.
  - **Location:** off for now, because all contract work is treated as remote.

### Where the results go
- `data/matches.csv`: matches that passed every check.
- `data/matches_flagged.csv`: matches that failed a check, with the reason.

Both files only ever grow. Re-running `match` adds nothing new. Changing a setting adds a fresh set of rows, and the export shows only the newest.

### Tuning
Every number above is a first guess, kept in `config/settings.yaml` under `matching:`. They'll be adjusted once more postings are in and reviewers start approving and rejecting matches. A change there takes effect on the next `match`; no code change is needed.

### Known limits
- **Small sample:** only 3 test jobs so far, so the weights are guesses.
- **Thin profiles:** the 15 contractors without a profile match less reliably. "Integrated Producer" still misses the campaign producer job, because the model doesn't see those titles as close. Filling in profiles will help most.
- **Pay gap:** the postings so far pay about $34–41/h (salaried), while contractors with a stated rate ask $70–150/h. This is shown as a note, not a filter (team decision).
- **Taxonomy coverage:** new skill words must be added to `config/skills_taxonomy.yaml` by hand. 15 media and content skills were added in this change.
- **Freshness reminder:** freshness is still 999 days.

### Changed in this commit
- **New commands:** `match`, `export-matches`.
- **New code:**
  - `match/scorer.py`, `match/gates.py`, `match/run.py`, `match/export.py`
  - `normalize/must_have.py`
  - title cleaning in `embed/texts.py`
- **Embed commands:** `embed-jobs` and `embed-contractors` now also build title vectors.
- **Config:**
  - `settings.yaml` → `matching:` (all tuning knobs)
  - `normalization.yaml` → `must_have_soft_markers`
  - `skills_taxonomy.yaml` → 15 media/content skills, plus prototyping and wireframing
- **Data:** new files `data/matches.csv`, `data/matches_flagged.csv`, and title vectors in `data/embeddings/`.

### Checks run
- **Tests:** 494 automated tests pass, 81 of them new; lint is clean.
- **Real run on 3 jobs × 42 contractors:**
  - 13 shortlisted and 1 flagged, in 0.09 s.
  - A second run added nothing.
  - The export and `validate-data` are clean.

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
