# Log update

What each change solves, for the team. Newest first. Add an entry with every commit.

---

## 2026-11-08 · Talent IDs instead of codenames, summary reasons, score colours, multi-file jobs, smaller dev data

### What this solves
- **No more "C34 is a senior writer…", and no talent IDs either.** The reasons, requirements and warnings name nobody, because the contractor box already shows the name, Profile and Resume (user rule).
  - It's a rule in the matching prompt (now `2026-11-08.v4`): describe the contractor directly ("Senior copywriter with…") and use the codename only in the answer's alias fields.
  - The prompt is shared by every provider, so the rule carries over if the model or provider changes.
  - Safety net: if a model still writes a codename, the app drops it ("C34 is a senior writer" becomes "Senior writer"; other mentions become "this contractor").
  - Evidence quotes stay word for word.
  - A first version earlier the same day put talent IDs in place of codenames (prompt v3). The user reversed that after seeing it.
- **Reasons are always 2 sentences** for fit and partial contractors (at most 45 words): why they fit, then the main gap or what to confirm. "No" verdicts keep one short sentence.
- **More repeatable answers:** a fixed `seed` is sent with each request (`llm.seed`, picked at random once). `temperature` is now 1, Google's default for Gemini 3 models. Judged jobs are cached, so a job's answer never changes on re-runs.
- **Verdict chip colour by score** (`app.score_bands` in `settings.yaml`):
  - 90+: darker green (`#5BBE77`);
  - 70–89: green;
  - 40–69: yellow;
  - below 40: light yellow;
  - no score: the verdict's colour.
  - Only the "fit" / "partial" chip is coloured; "· score 95" is plain text.
- **Contractor rows without a talent ID** (in a `.csv`) get **`TAL-CAN-<highest number + 1>`**, so with `TAL-ON-052` known the next is `TAL-CAN-053`.
  - Loading the same file again reuses the ID by matching the name, so no duplicate is made.
  - Names are compared on this machine only. The run log lists the IDs given (IDs only).
  - The file itself is never changed. Add the IDs to it to keep them stable.
- **Several job files at once** in the Browse dialog (and the browser preview's upload box).
- **Smaller development data:** `data/jobs.csv` now holds 23 jobs instead of 82 (10 with a clear fit, 10 with a partial, 3 with no match, spread over as many professions as possible). This keeps Gemini calls low while developing.
  - The full list is in `data/jobs.csv.backup`, and a timestamped copy is in `data/backups/`.
  - One no-match job ("business analyst - IT III") was swapped for "data integration architect" (user request).
  - **`data/inbox/` was moved to `data/inbox.before-dev-trim.20261108/`.** Every run imports every inbox file, so leaving it would have brought the 59 removed jobs back. The app creates a fresh inbox when a job file is added.
- **Re-judged with prompt v3** (2026-11-08):
  - 23 calls, all usable; 207,654 tokens (151,486 in, 56,168 out; about 9k per call); 24 of 500 requests used today.
  - Result: 13 jobs with clear fits, 8 with a partial, 2 with no match (32 shortlist rows).
  - No codenames are left in the text; 17 reasons name a talent ID. Reasons run 14–28 words.
  - 4 answers contradicted themselves ("no_fit" true while some contractors were rated fit or partial). As before, the rated contractors still count, so nobody is hidden.
- **Re-judged again with prompt v4** (same day, after the no-ID and 2-sentence rules):
  - 23 calls, all usable; 207,482 tokens (152,590 in, 54,892 out). Today's total is 415,136 tokens and 52 of 500 requests; the request counter includes about 5 retried requests.
  - Result: 14 jobs with clear fits, **1 with a partial**, 8 with no match (24 shortlist rows).
  - Every fit/partial reason is 2 sentences (21–35 words, average 28). No codename, talent ID or "this contractor" appears in any text.
  - ⚠ Partials dropped from 8 to 1, and no-match jobs rose from 2 to 8, compared with v3. A free comparison of the saved answers showed the prompt was the cause: v4 called same-field people with one missing skill "no", and turned real gaps into "confirm X" fits.
- **Prompt v5 (same day): verdict by the size of the gap** (user scale):
  - **fit:** same profession and meets the required skills; missing a little = fit with a lower score.
  - **partial:** missing a few required skills, or one level off in seniority.
  - **no:** the gap is too big (different profession, or missing many required skills).
  - The score must agree with the verdict: fit with nothing missing 90–100, fit missing a little 70–89, partial 40–69. These match the chip colours.
  - Summaries by verdict: a partial says what fits and what keeps them from a clear fit.
  - A required item not written in the card is a gap, never "to confirm".
  - Met and missing items must be specific (the job's requirement plus what in the card meets it), not generic.
  - `max_output_tokens` was raised to 8,192 so longer answers are never cut off.
  - A new check flags a score that disagrees with its verdict.
  - **Re-judged:** 23 calls, all usable, 216,060 tokens. Today's total is 631,196 tokens and 75 of 500 requests.
  - **Result:** 12 jobs with clear fits, 4 with a partial, 7 with no match. "Meets" items are longer and more specific (about 14.5 words, was 9), but still about 3 per contractor.
  - 4 shown contractors are "fit" with a 50–65 score, which disagrees with the verdict and is flagged.
- **Prompt v6 (same day): stopping invented matches.** Some v5 "Meets" items claimed things the contractor's card doesn't say.
  - **Each requirement met now carries a quote from the card,** and the app checks the quote word for word, like the evidence quotes. An item whose quote isn't in the card is dropped before anyone sees it. On screen it reads `Requirement — "card words"`.
  - **The answer is written evidence first:** requirements met and missed, red flags and evidence, then the score, then the verdicts. That way the score follows from the evidence instead of being justified after the fact.
  - **The score decides:** a "fit" scored under 70 is kept as a partial.
  - New `llm-try --thinking-level` option to try another thinking level on one job without touching shortlists.
  - **3-job test** (social media community manager, email designer, bilingual partnership), each with thinking `minimal` and `low`:
    - 6 calls, 58,083 tokens.
    - Both levels reported 0 thinking tokens and gave similar answers, so `low` showed no benefit and the setting stays `minimal`.
    - v6 answers: Todd is now partial at 50 (was fit 85, then 75); the graphic designers are partial at 40–60 for the email-designer job (missing HTML email coding); the bilingual job is partial, with French listed as missing.
    - Quotes stop invented text, but a real quote can still be stretched (for example "Art Director" given as proof of an art degree).
- **Prompt v7 (same day): plain English and real proof points.**
  - **Plain English:** the prompt says "profile", never "card", and tells the model to write for a recruiter with no technical words ("card", "alias", "verdict", field names). If one slips through, the app swaps it for everyday words ("Card does not mention SEO" becomes "Their profile does not mention SEO").
  - **Details are proof points,** not justifications. Each bullet is a concrete fact from the profile (a result, number, budget, client, tool, credential or years of experience), such as "Managed 40+ staff and $10M+ annual budgets". It never restates the job's requirement. Each is checked against the profile behind the scenes, and only the fact is shown.
  - **More of the profile is sent to Gemini** (user decision: all details except the name): proof points, location (city/province), rate expectation, weekly hours and the whole summary, in addition to what was sent before.
    - The name and the talent ID are still never sent.
    - Emails, phone numbers, links, street addresses and postal codes are still scrubbed from every text.
  - Each call sends about 1.5k more tokens: measured about 8.6k in, versus 6.9k before.
  - CLAUDE.md V1 rule 2 was updated by the user to allow proof points, the executive summary and city + province.
  - **3-job test:** 3 calls, 34,373 tokens. **Full re-judge:** 23 calls, all usable, 254,088 tokens (196,888 in, 57,200 out). Today's total is 977,657 tokens and 108 of 500 requests.
  - **Result:** 5 jobs with clear fits, 12 with a partial, 6 with no match.
    - Proof points per shown contractor: 1–6, average 2.8, all real facts from the profile ("Built brand systems for 20+ enterprise clients").
    - No technical words in any text.
    - Still open: some "fit" verdicts miss two or three required items (for example a degree and design software), which is lenient on the user's scale. One partial scored 75, above the partial range (flagged).
- **Prompts v8 and v9 (same day): the recruiter flow and weighted verdicts** (user design).
  - **Flow:** read the job → screen every contractor in one sentence → deep review of the best 5. Each review has a justification (2 sentences), then the description (proof points, missing, warnings, evidence), then the score, then the final verdict. **The app shows the best 3 of those 5,** ranked by final verdict (fit before partial), then score. A deep review's verdict and justification replace the screening; changes are logged.
  - **Weighted verdicts, no counting rule:** what the job is mainly about counts most. Anything in the job title or stated as mandatory or required is core. Missing a core item is at most partial (example: a Python project without Python). Missing a few smaller items is still fit, with a lower score.
  - **Proof points are achievements, results, clients, tools, credentials or years,** never location, availability, rate, level labels or admin notes. Fewer is better than filler.
  - v8 test (3 jobs, 36,876 tokens): the bilingual job got 2 fits without French, and filler proof points appeared ("Based in Vancouver", an admin note "Contractor agreement countersigned…" taken from one contractor's proof points in the list). v9 fixed both: the 3-job test (35,286 tokens) showed the bilingual job as partial with French flagged as core, and no filler.
  - **Full re-judge with v9:** 23 calls, all usable, 264,973 tokens (199,648 in, 65,325 out). Today's total is 1,314,792 tokens and 138 of 500 requests.
  - **Result:** 9 jobs with clear fits, 13 with a partial, 1 with no match (30 shortlist rows).
    - Proof points per shown contractor: 0–5, average 2.0; 3 contractors have none (thin profiles).
    - No technical words. The deep review changed the screening 3 times, all fit to partial.
  - Data note: one contractor's proof points in the list include an admin note (contract countersigned date). Clean it in the workbook.
- **Prompt v10 (same day): a "Meets" table, and no separate evidence.**
  - **"Evidence" is gone** from the answer, the prompt and the screen. It repeated what the checked rows already prove, and cost output tokens.
  - **"Meets" is a two-column table:** *TALENT ASSET* (their skill or result) | *JOB REQUIREMENT* (column names chosen by the user).
    - The left side is copied from the profile (core strengths first, then proof points).
    - Each row also carries the requirement copied from the posting. Both are checked word for word, and only then is the requirement's short summary (8 words or fewer, as asked) shown on the right.
    - A row that fails either check is dropped before anyone sees it.
  - Stored as `skill → requirement` in the existing "Meets" column, so `llm_matches.csv` didn't change shape.
  - Also fixed: a developer note (a class docstring) was being sent to Gemini as part of the answer layout since v7. Now only the short field descriptions are sent.
  - **3-job test:** 3 calls, 35,719 tokens; 1 of about 37 rows dropped.
  - **Full re-judge:** 23 calls, all usable, 261,399 tokens (198,659 in, 62,740 out). Today's total is 1,611,937 tokens and 164 of 500 requests.
  - **Result:** 10 jobs with clear fits, 12 with a partial, 1 with no match.
    - 181 table rows kept and 7 dropped by the word-for-word check (under 4%), so Gemini copies reliably.
    - Rows per shown contractor: 1–5, average 2.7. Requirement summaries are 2–8 words, average 4.8.
  - Still open: 4 partials scored 72–75 (above the partial range, so their chip shows green), and some pairings are a stretch (for example "AI transformation" paired with "operate automated community management systems").
- **Prompt v11 (same day): the score decides, and the field of work counts.**
  - **The score sets the verdict:** 70+ is fit, 40–69 is partial, below 40 isn't shown. It's in the prompt ("score first, then the verdict follows") and enforced by the app in both directions, so a partial chip is never green and a fit chip never yellow.
  - **Field of work, not just the title:** a project manager, analyst, designer or writer from a different field is at most partial when the job's work depends on that field. The case behind it: a digital and marketing programs project manager scored 75 for an IT software and infrastructure project manager job.
  - **3-job test** (Project Manager, social media, bilingual): 3 calls, 35,691 tokens. Today's total is 1,647,628 tokens and 167 of 500 requests.
    - The score-decides rule works: every label now matches its score.
    - **But the model ignored the two judgement rules.** The marketing-programs project manager still scored 75 (fit) for the IT project manager job, and the bilingual job still has 2 fits lacking French.
    - The full re-judge was held back, since it wouldn't fix what was asked.
- **v12 (same day): two models per job, with a fallback chain** (user design).
  - **Thinking test first:** at thinking `medium` the same model applied both rules (Kayla partial 65, the bilingual job with French flagged). At `high`, both calls were cut off at the 8,192-token output limit.
  - **Screening (Flash Lite, thinking `medium`):** reads every contractor, gives one-line verdicts, and **shortlists only those who fit or partly fit** (up to 5; fewer if fewer fit; none means no second call and "no match").
  - **Deep review (stronger model, thinking `medium`):** only the shortlisted profiles are sent. It writes the justification, Meets table, missing items, flags, score and final verdict.
  - **Review chain:** `gemini-3.6-flash` → `gemini-3-flash-preview` → `gemini-3.5-flash-lite`.
    - A model that answers "daily limit reached", or **doesn't answer within 120 s** (not retried), is skipped for the rest of the run, and the next model reviews. The next run tries it first again.
    - `gemini-3.8-flash` was tried first and removed: it answered "high demand" (HTTP 503) or timed out on every review.
    - `gemini-3.0-flash` doesn't exist for our key, so `gemini-3-flash-preview` stands in for it (the model list call was checked).
  - **Per-model quota:** usage is counted per model, and the run log shows today's use for each. Review-model limits in `settings.yaml` are guesses (20 requests a day) until copied from AI Studio.
  - **Also:**
    - The judgment cache key now includes the thinking setting.
    - A screening answer is saved on its own, so a failed review is retried without screening again.
    - `max_output_tokens` is 12,288 (thinking counts against it).
    - `llm-try` runs both calls and saves to its own folder, never the run cache.
  - **3-job test** (6 calls, 55,407 tokens): Kayla is **partial 58** ("missing the mandatory PMP certification and explicit provincial-scale IT infrastructure project experience"). The bilingual job has no fit.
  - **Full re-judge:** 40 calls (23 screenings, 17 reviews; 6 jobs had nobody shortlisted), all usable, **405,239 tokens**. Today's total is 2,207,352 tokens: Flash Lite 209 of 500 requests, 3.6 Flash 20 of a guessed 20. No fallback was needed.
    - Reviews cost 3.5k–10.2k tokens each, mostly thinking.
  - **Result:** 3 jobs with clear fits, 10 with a partial, 10 with no match.
    - The reviewer reviewed 59 contractors in depth and **downgraded 30 screening verdicts** (25 partial to no, 5 fit to partial), never upgraded.
    - No technical words in any text.
- **Prompt v13 (same day): fairer verdicts and a legal guardrail** (user decisions after a review of all 23 jobs).
  - **"No" only for a different profession, or when almost none of the core is there.** The same profession missing a core specialty is partial (40–69). Examples in the prompt: a graphic designer without HTML email coding; an HR leader without compensation design.
  - **Personal traits:** the model must never infer, guess or weigh age, gender, race, ethnicity, national origin, religion, disability, family status, sexual orientation or any other personal trait, not even from years of experience, names or dates. The prompt states this is **illegal discrimination under Canadian human rights law**. Eligibility conditions (age-based programs, citizenship, permits, clearance) become a flag for the recruiter to confirm, never a gap or a lower score.
    - The case behind it: the reviewer had inferred that a contractor with 10+ years of experience was over 30 for a youth-subsidy job.
  - **Missing items:** only what the job requires, never nice-to-haves or assets. English is taken as given (Canada); French is not.
  - **Screening leans generous:** when unsure between partial and no, it chooses partial and shortlists, and the deep review decides.
  - **3-job test** (new API key; 6 calls, 67,350 tokens):
    - ECH marketing manager now shows 5 partials (it had none before; the screening had rated everyone "no").
    - The email designer's graphic designers are all partial (40–55).
    - The event coordinator: no age inference; each candidate gets the flag "the recruiter should confirm the candidate is 30 or under and a Canadian citizen, permanent resident, or refugee as required by the wage subsidy program".
    - With the new key, `gemini-3.6-flash` still answered "daily limit reached", so the chain fell back to `gemini-3-flash-preview` as designed.
    - Seen: the model also adds a generic "confirm legal eligibility to work in Canada" flag even when the posting doesn't ask.
- **Prompt v14 (same day):** eligibility flags only when the job text states the condition.
  - `max_output_tokens` raised to 24,576: a `gemini-3-flash-preview` review thought for 11k tokens and was cut off at 12,288.
  - **Full re-judge (second API key):** 43 calls, all usable except that one cut-off review, which was redone after the raise (1 call). 453,964 tokens in all.
    - Reviews: 22 jobs by `gemini-3.6-flash`, 1 by `gemini-3-flash-preview` (3.6 Flash answered "daily limit reached" once mid-run; the chain fell back).
    - Today's local total: 2.73M tokens across both keys.
  - **Result:** 4 jobs with clear fits, 14 with a partial, 5 with no match (was 3 / 10 / 10 in v12).
    - The reviewer changed 27 screening verdicts (18 partial to no, 8 fit to partial, 1 fit to no). Screening now shortlists generously.
    - Eligibility flags: 5 across all reviews (v13 had put one on every candidate). No age mentions in any shown text.
  - **Run report** with timing (about 32 s per job), token usage per model and quality metrics: [reports/2026-11-08-matching-run-v14.md](reports/2026-11-08-matching-run-v14.md).

### How to use
- Contractor IDs: nothing to do. If the app reports assigned `TAL-CAN-…` IDs, copy them into your source file.

### Checks run
- `pytest`: 732 passed (8 new: codenames dropped from the model's text with English and French examples, evidence left unchanged, TAL-CAN numbering and reuse, seed, score bands and chip colours).
- `ruff check .` and `ruff format --check .`: pass.

---

## 2026-11-07 · The app is now "do good talent", in the org's light purple

### What this solves
The desktop app carries the org's name and colours.
- **Name:**
  - The header, the window title and the Mac Dock show **do good talent**.
  - The app files are **`dogoodtalent.exe`** (Windows) and **`dogoodtalent.app`** (Mac).
  - The downloads are `dogoodtalent-mac-intel.dmg` and `dogoodtalent-windows.zip`.
- **Data folder:** now `dogoodtalent`: `~/Library/Application Support/dogoodtalent` on Mac, `%APPDATA%\dogoodtalent` on Windows. Nobody had installed the app yet, so no data needs moving.
- **Theme:** light purple.
  - The header and main buttons use a medium purple (`#7E57C2`), so white text stays readable.
  - The page background is a light purple (`#EDE7F6`).
  - The green/yellow/red decision colours are unchanged, because they carry meaning.
- **Unchanged:** the code package, the `jobmatch` CLI command, Docker and the repo layout. Only what end users see was renamed.

### How to use
Nothing to do. The next build produces the renamed files. [FIRST_LAUNCH.md](FIRST_LAUNCH.md) uses the new names.

### Checks run
- `pytest`: 724 passed. The screen test now also checks the header says "do good talent", and the folder tests use `dogoodtalent`.
- `ruff check .` and `ruff format --check .`: pass.
- The purple isn't covered by tests; check it by eye in the preview or the first build.

---

## 2026-11-07 · CI on every push, releases from a tag

### What this solves
- **Mistakes are caught on every push**, not only when someone builds the app. The new `ci.yml` runs lint, the format check and all tests on Linux (about 2–3 minutes). A ✓ or ✗ appears next to each commit on GitHub.
- **New app versions have a permanent download page.** Before, builds were only downloadable for 14 days, from the run's page, and needed a GitHub login. Now pushing a tag such as `v0.2.0`:
  - builds and checks both apps;
  - puts them on a **draft** GitHub Release that only repo owners see;
  - waits for an owner to click Publish.
- **One version number.** `pyproject.toml` sets it. The Mac app now reads it too (it was hard-coded), and a tag that doesn't match it is refused before anything is built.
- **Not included (user decision):** auto-update inside the app. It would need a network call that rule 4 doesn't allow, and on an unsigned Mac app it would bring back the "Open Anyway" warning.

### How to use
See [DEVELOPMENT.md](DEVELOPMENT.md), "CI and releases". In short: raise the version, push, tag `v<version>`, push the tag, check the draft, then publish it.

### Checks run
None yet. Workflows can only run on GitHub, so the first push and tag are the test.

---

## 2026-11-07 · DataLoader factory: one place decides where records are kept

### What this solves
Storage can move from CSV files to a database (MongoDB was asked for) without touching the rest of the code.
- **`storage/loader.py`:**
  - **`create_loader(settings)`** returns a **DataLoader** with every repository: jobs, contractors, Gemini shortlists, reviews, runs and the Gemini usage log.
  - **`storage.backend`** in `config/settings.yaml` picks the backend: `csv` (default, the same files as before) or `mongodb`.
- **MongoDB is a placeholder for now** (user decision). Choosing it stops with "MongoDB storage isn't available yet: set storage.backend to csv". Nothing is installed or sent anywhere. Where it would run (a local server, or a hosted one, which needs CLAUDE.md rule 4 and a privacy decision) is still open.
- **Rewired code:**
  - The CLI, `run-once`, the desktop app, ingest and the export now use the loader.
  - Contractor writes (`build-contractors`, contractor upserts, `apply-professions`) also go through it.
  - A test fails if code outside `storage/` starts building CSV stores again (`validate-data` excepted, since it's a CSV checker).
- **Unchanged:** the CSV files (same names, columns and backups). The raw page cache and Gemini's cached answers stay as files.
- **One small difference:** when `build-contractors` writes the app's own contractor list, it's now a normal internal CSV, not the Excel-style export it was before (no byte-order mark or formula protection). The content is the same. Writing to any other `--out` file is still an export.

### How to use
Nothing to change. `storage.backend: csv` is the default.

### Checks run
- `pytest`: 724 passed (7 new for the loader). `ruff check .` and `ruff format --check .`: pass.
- Also fixed a flaky screen test: it could check a different contractor box from the one it clicked.

---

## 2026-11-06 · Desktop app builds (G3): Mac .app and Windows .exe on GitHub Actions

### What this solves
End users get a real app to install. There's no Mac here to build one, so GitHub Actions builds both platforms.
- **`.github/workflows/build-app.yml`** builds on `macos-15-intel` (GitHub's last Intel Mac runner, until fall 2027) and `windows-latest`. Each build:
  1. installs exactly what `uv.lock` pins;
  2. runs the tests, with Gemini faked and no key;
  3. builds with PyInstaller;
  4. runs the finished app with `--check`;
  5. uploads **`jobmatch-mac-intel.dmg`** and **`jobmatch-windows.zip`**, kept 14 days.
- **`packaging/jobmatch.spec`** bundles the code, NiceGUI's and pywebview's files and `config/`. It never bundles `data/`, `exports/` or `.env`.
- **`--check`:** the app loads its config and every module, opens no window, writes `check.txt` and exits 0 or 1. A missing piece fails the build, not the user's first launch.
- **[FIRST_LAUNCH.md](FIRST_LAUNCH.md)**, for end users:
  - installing;
  - the one-time "unidentified app" confirmation (Mac: System Settings › Privacy & Security › Open Anyway; Windows: More info › Run anyway);
  - the Gemini key, a first run, reviewing results, where data lives, and troubleshooting.

### How to use
1. Push the branch.
2. On GitHub, go to **Actions › build-app › Run workflow**.
3. When it finishes, download the two files from the run's **Artifacts** section.

### Known limits
- Built and checked on GitHub's machines only. The real window, the file dialog and the first-launch prompt still need one check on the client's 2019 MacBook and on a Windows PC.
- Unsigned: macOS and Windows ask for a one-time confirmation (see FIRST_LAUNCH.md).
- The first runs may need 1–3 fixes (a missing hidden import or file). The build log shows which.

### Checks run
- `pytest` (Linux, Docker): 717 passed (3 new for `--check`). `ruff check .` and `ruff format --check .`: pass. The build machines also run the tests on macOS and Windows.

---

## 2026-11-06 · Desktop app: design pass on results and Find matches

### What this solves
Clearer results cards, a visible progress bar, and decisions that can be undone.
- **Job cards:**
  - The job title is shown in Title Case. Words with capitals, like UX or SQL, are kept, and the stored data doesn't change.
  - The title band is a light shade: green for clear fits, yellow for best-partial jobs, none for no match.
  - **Open posting** is a button with an icon.
- **Decision buttons:** **Approve / Reject / Needs info** have light green / red / yellow backgrounds.
- **Decision tag and Reset:** once you decide, the decision tag and a **Reset** button appear at the top right of the contractor's box.
  - Reset adds a `cleared` row to `reviews.csv`. The file stays append-only, so the history is kept, and the match counts as undecided again.
- **Details:** **Profile** opens a read-only pop-up of the contractor's row in `contractors.csv` (column name | value). **Resume** is a placeholder for a later version.
- **Find matches:**
  - The button is centred.
  - A progress bar shows each stage: contractors, job import (one step), one step per Gemini call, then saving.
  - `run_once` reports the stages through a new `progress` callback; the CLI ignores it.

### Checks run
- `pytest`: 714 passed. `ruff check .` and `ruff format --check .`: pass.

### Follow-up tweaks (same day)
- The contractor's header row has a fixed height, so the decision tag and Reset appear without pushing anything down.
- **Open posting** is an icon-only button, with a tooltip and a screen-reader label.
- Approve / Reject / Needs info backgrounds are two steps lighter. The decision tag keeps the stronger shade.
- **Sizing follows the window:**
  - Screens use the full window width, with side padding that grows with the window, up to 1600 px; Settings goes up to 900 px.
  - The progress log is a third of the window height.
  - The Profile pop-up is up to 90% of the window wide and 80% high.
  - Titles, badges and decision buttons wrap on narrow windows. Contractors stay one per row (user choice).
  - Past those caps, each screen stays centred in the window; the header keeps the full width.
- **Job card layout:**
  - The title band holds only the job title, shaded green for clear fits and yellow for best-partial jobs.
  - Company, location and the matching result are three chips below it; only the result chip is tinted.
- **Contractor box border:** its left border shows **your decision**: green for approved, red for rejected, yellow for needs info, and none when undecided. It updates on Approve / Reject / Needs info / Reset. The text doesn't shift sideways when the colour appears.

---

## 2026-11-06 · Desktop app screens (G2)

### What this solves
End users get screens instead of commands (`src/jobmatch/app/`):
- **Find matches.** Pick a contractor file (optional) and a jobs file, then click **Find matches**.
  - A jobs file with only links goes to `inbox/links/`; anything else is treated as a pasted or saved posting. Copies never overwrite.
  - Progress shows live, one line per Gemini call with its tokens, plus today's quota.
  - If the run would use most of the quota, a dialog asks first.
- **Results.** One card per job:
  - **Clear fits** (up to 3), **No clear fit: best partial, check by hand**, **No match**, or **Not judged yet**.
  - For each contractor: name, verdict, score, reason, and details (requirements met and missing, flags, checked evidence).
  - **Approve / Reject (with a reason) / Needs info**, saved to `reviews.csv`. The latest decision shows on the card.
  - **Export to Excel**, and **Add jobs / new search**, which goes back to the first screen.
- **Settings.** The Gemini key is saved to the app's `.env` and never shown back. Also the reviewer name, and where the data folder is.

The logic is in `app/service.py` (no UI code, tested directly); `app/ui.py` only lays out the screens. `export-llm-matches` and the Export button share `llm/export.export_current`.

### How to use (preview, before the packaged app)
```
docker compose build
docker compose run --rm --service-ports app python -m jobmatch.app --browser --host 0.0.0.0
```
Then open http://localhost:8080. The preview uses the repo's `data/`, so **Find matches is a real run**: jobs judged before cost nothing, new jobs cost one Gemini call each, and the quota dialog appears first.
- The file pickers are upload boxes in the preview; the packaged app uses the normal Mac/Windows file dialog.
- A key saved in the preview's Settings lives only inside that container; Docker already reads the key from `.env`.

### Checks run
- `pytest`: 709 passed (17 new: the service, and three screens through NiceGUI's simulated user). `ruff check .` and `ruff format --check .`: pass.
- The image installs the `app` extra: 491 MB, previously 383 MB.
- The preview served `/`, `/results` and `/settings` (HTTP 200) with no errors in its log.

---

## 2026-11-06 · Desktop app groundwork (G1): no Docker, Mac and Windows

### What this solves
The desktop app runs without Docker. This change prepares the code for that; the app's screens come next (G2).
- **Dependencies for the app** are an optional `app` extra: `nicegui` (the UI) and `pywebview` (a native window). `pyinstaller` is in a `build` group. Docker installs neither, so the image is unchanged.
- **`uv.lock` now covers Intel Mac and Windows** as well as Linux, with the same 2026-10-05 cutoff. The base packages kept their versions; 57 packages were added for the app and the build.
  - **Every compiled package has an Intel Mac wheel** (the oldest requires macOS 10.12–10.15).
  - **Every package the app needs on Windows has a Windows wheel.** `uvloop` is skipped there by design.
  - `proxy-tools` (needed by pywebview) is source-only but pure Python, so it installs without a compiler.
- **The app's own folder** (`app_paths.py`) holds `data/`, `exports/` and `.env`:
  - Mac: `~/Library/Application Support/jobmatch/`;
  - Windows: `%APPDATA%\jobmatch\`.
  
  The config ships read-only inside the app.
- **`load_settings(..., data_root=...)`**, or `JOBMATCH_DATA_DIR`, points `data/` and `exports/` at that folder. The CLI and Docker behave as before.
- **`.env` without Docker** (`secrets_file.py`, no new dependency): reads the Gemini key at startup and saves it from the app. The file is readable by its owner only, and the key is never logged or shown.

### How to use
Nothing new to run. After pulling, `docker compose build` is optional (the lock changed, but not the packages the image installs).

### Checks run
- `pytest`: 692 passed. `ruff check .`: pass.

---

## 2026-11-06 · Shortlist rule: up to 3 fits, else the best partial

### What this solves
Keeping every fit and partial made shortlists too long: a median of 7 contractors per job and up to 20. The reviewer now sees what matters:
- **A job with clear fits** shows the fits only, up to 3, best first.
- **A job with no clear fit** shows its single best partial fit, for a person to check.
- **A job with neither** shows 0 ("no match").

"Best" is Gemini's detailed order, then any contractor it judged without describing them, so a self-contradicting answer still can't hide a fit. Gemini still judges every contractor, and the rest stay in its cached answer. Changing the numbers (`llm.shortlist` in `config/settings.yaml`) needs no new API call.

### How to use
Nothing new to run. `run-once` and `export-llm-matches` apply the rule.

### Results on our data (82 active jobs, 0 Gemini calls)
- `llm_matches.csv` was rebuilt from the cached answers (the old file is in `data/backups/llm_matches.before-shortlist-rule.20261106T222251Z.csv`): **97 rows**, previously 384.
- **37 jobs with clear fits:** 17 show 3 fits, 5 show 2 and 15 show 1.
- **21 jobs with only partial fits** show their best partial.
- **24 jobs** show no match.
- 3 of the 97 rows are "reason only", where Gemini didn't describe the contractor.

### Checks run
- `pytest`: 679 passed. `ruff format --check .` and `ruff check .`: pass.

---

## 2026-11-06 · v0 matcher removed; fits Gemini didn't describe are kept

### What this solves
- **One matcher, a much lighter app.** v0 (local embeddings with bge-m3) is gone from this branch; it lives on a separate local branch. That removes torch, transformers, sentence-transformers and numpy, the 4.5 GB model download and the Hugging Face cache volume, and makes a desktop app without Docker possible.
- **No hidden candidates.** Gemini sometimes judges a contractor fit or partial but leaves them out of its detailed list (one real answer said "nobody fits" while rating two people fit or partial). Those contractors are now stored too, after the detailed ones, as **reason only** rows (no score or evidence), so a person can check them.

### What changed for you
- **Removed commands:** `smoke-embed`, `embed-contractors`, `embed-jobs`, `match`, `export-matches`, `export-trial-run`, `import-reviewed-matches`, `evaluate-matches`. Also `scripts/update_contractors.sh`, `scripts/eval/` and `data_visualizer.ipynb`. Use `run-once` and `export-llm-matches` instead ([quick_start.md](quick_start.md)).
- **`llm-compare`** now compares Gemini with the blind labels only (no v0 columns).
- **`exports/llm-matches-*.csv`** has a new `detail` column: `full`, or `reason only`.
- **Old v0 data** (`data/matches.csv`, `data/matches_flagged.csv`, `data/embeddings/`) is left in place and no longer read. Delete it whenever you like.
- `.env`: `HF_HUB_OFFLINE` is no longer needed. The `hf-cache` Docker volume can be removed (`docker volume rm`, about 4.5 GB).

### How to use
After pulling: `docker compose build`, then `run-once` as usual. Answers already cached are reused, so no new Gemini calls.

### Results on our data (82 active jobs, 0 Gemini calls)
- `llm_matches.csv` was rebuilt from the cached answers (the old file is in `data/backups/llm_matches.before-detailed.20261106T221351Z.csv`): 384 rows, previously 214.
  - **Described (unchanged):** 214 rows, 89 fit and 125 partial.
  - **Reason only (new):** 170 rows, 5 fit and 165 partial. Mostly partial fits beyond the 5 Gemini describes per job (`llm.top_k`), in 36 jobs.
- **3 jobs moved from "no match" to "matched"** because Gemini had rated someone fit or partial while saying nobody fits. 58 jobs now have a shortlist and 24 have none.
- Shortlists have a median of 7 contractors per job and a maximum of 20.

### Checks run
- `pytest`: 676 passed (809 before: 135 v0 tests removed with v0, 2 added for reason-only rows). `ruff format` and `ruff check .`: pass.
- `uv lock`: 33 packages, previously 57. Every remaining package kept its version, and none was added.
- Docker image `jobmatch:dev`: 383 MB, previously 2.08 GB. The `hf-cache` volume (5.1 GB) was removed.

---

## 2026-11-06 · v1 phase 6: one-button run with Gemini (`run-once`)

### What this solves
One command now runs the whole v1 pipeline. It is also the backend for the planned desktop app's "Find matches" button. v0's matcher is not used anywhere in it.
- **`run-once`**:
  1. optionally loads a contractor file (`--contractors`);
  2. imports links and pasted postings from `data/inbox/`;
  3. checks today's Gemini quota;
  4. judges every active job that hasn't been judged yet, one call each (cached answers cost nothing);
  5. stores the shortlists and sets each job to `matched` or `no_match`.
  
  If the run would use most of today's quota, it asks first. `--yes` goes ahead (or, if the run can't finish today, sends only what fits); `--max-calls N` caps it.
- **Loading contractors (upsert).** A `.xlsx` master workbook or a `.csv` in the contractor list's columns. New talent IDs are added and changed ones are updated. Contractors missing from the file are kept, so removal is still to be decided. The list is backed up before every change.
- **`data/llm_matches.csv`**: one row per shortlisted contractor with the verdict, fit score, reason, requirements met and missing, red flags and verified evidence. Rows are never changed. A job's current shortlist is the rows of its current judgment.
- **`export-llm-matches`**: a spreadsheet of the current shortlists with contractor names (keep it local). Jobs with no match, or not judged yet, get one row saying so.

### How to use
```
docker compose run --rm app python -m jobmatch run-once --contractors data/contractors/contractor-master-list.xlsx
docker compose run --rm app python -m jobmatch export-llm-matches
```

### Known limits
- Skills are not learned from a loaded contractor file. That fed v0's embeddings only.
- The intake form's file format is not known yet. A `.csv` must use the contractor list's columns.

### Checks run
- `pytest`: 809 passed, 1 deselected. `ruff format --check .` and `ruff check .`: pass.
- The old CLI test that ran `run-once` against the real `data/` was replaced by one using a temp config and data folder. Before that fix, a test run added two ingest rows to `data/runs.csv` (2026-11-06): 0 pages fetched, 0 new jobs, no API call.

---

## 2026-11-05 · v1 phase 5: Gemini pilot on the blind-labelled jobs

### What this solves
We needed to know whether Gemini matches better than v0 before replacing it.
- **`llm-judge`** judges jobs (`--labelled`, `--job-id`, `--exclude-language`, `--max-calls`, `--dry-run`) with the budget check, pacing and one usage line per call. Answers are cached in `data/llm_results/judgments/`, so a re-run only pays for what changed.
- **`llm-compare`** compares the cached answers with the blind labels and with v0's latest stored run (scoring version `0ceca894e984`, read from `matches.csv`; v0 is not re-run). No API call. It writes a pair table to `data/llm_results/compare-<time>.csv`.

### Results (78 jobs, 328 labelled pairs; the one French posting was left out)
- **Cost:** 78 calls, 0 failed, 690k tokens (8.9k per call; the estimate was 10.5k), 16 minutes. 18 requests were per-minute retries.
- **Ranking within a job, fit above "no":**
  - Gemini 0.88, v0 shortlist 0.856. The difference is +0.024, but the 95% interval (-0.044 to +0.084) includes zero.
  - v0's full-score figure, recorded in PROTOTYPES.md, is about 0.87.
- **Put in front of the reviewer:**
  - Gemini (fit or partial): 88 of 99 fits, 37 of 181 "no"; "no" is 24% of what it shows.
  - v0 shortlist: 82 of 99 fits, 46 of 181 "no"; "no" is 32% of what it shows.
- **Gemini's "fit" verdict is reliable:** 58 pairs, of which 48 are labelled fit and 1 "no". Its "partial" is mixed: 40 fit, 21 partial, 36 no.
- **Agreement with the labels:** 65%, kappa 0.43 (0.56 on labels both AI judges agreed on).
- **11 labelled fits were rejected,** 8 of them pairs the two AI judges disagreed on. Gemini applies "different profession" strictly (graphic designer for a UX role, art director for video production).
- **10 of 78 answers contradicted themselves** on `no_fit`, even with prompt v2.

### Known limits
- **The labels are AI judges (ChatGPT, Claude),** with 13 pairs by a person. Gemini is also an LLM, so its agreement with them may be inflated relative to v0.
- **The label sheets were sampled around v0's shortlists.**
- **The contractor professions sent to Gemini were suggested by Gemini itself.**

### Checks run
- `pytest`: 793 passed, 1 deselected. `ruff format` and `ruff check .`: pass.

---

## 2026-11-05 · v1 phase 4: first real Gemini calls, contractor professions

### What this solves
- **Contractor professions (user choice: let Gemini suggest them).**
  - `suggest-professions` makes one call over the 38 anonymized cards and writes `data/contractors/profession_suggestions-<time>.csv`.
  - `apply-professions <file>` writes the professions into `contractors.csv` after a timestamped backup.
  - Done on 2026-11-05: 38 of 38 answered, 36 "high" confidence and 2 "low" (TAL-ON-014, TAL-ON-020).
- **`llm-try`** judges one job with one real call, prints the token usage and verdicts (talent IDs and headlines only), and saves the checked answer to `data/llm_results/<time>/`.
- **`llm/runner.py`:** every call goes through one function that checks the call's size first and logs its usage afterwards, failed calls included.
- **Tests can never reach the real API:** the key is removed from the environment in every test.

### First results
- **Profession call:** 10,261 tokens (estimate 13,653).
- **Matching call:** 7,872 tokens (estimate 10,088). The local estimates run about 25% high, which is the safe side.
- **The verdicts followed the profession rule,** e.g. "Different profession…" for a technical project manager on a communications project role.
- **The answer contradicted itself:** 2 "partial" verdicts with `no_fit` true and no detailed matches. Prompt v2 says partial matches go in `top`, and `no_fit` only when every verdict is "no". `check_output` now reports both cases.

### How to use it
- `docker compose run --rm app python -m jobmatch llm-try [--job-id <id>]`: 1 call.
- `... suggest-professions` (1 call), then `... apply-professions data/contractors/profession_suggestions-<time>.csv` (no call).
- **Keep the professions:** `build-contractors` rebuilds `contractors.csv` from the workbook, so copy the suggested values into the workbook's profession columns, or they are lost on the next rebuild.

### Known limits
- **French postings without bullet lists send their whole clean description,** including benefits and employer text when normalization didn't split the sections (seen on the first job tried).
- **`thinking_level: minimal`** for `gemini-3.5-flash-lite` is a GUESS default; the calls reported 0 thinking tokens.

### Checks run
- `pytest`: 787 passed, 1 deselected. `ruff format` and `ruff check .`: pass.

---

## 2026-11-05 · v1 phases 0 and 1: Gemini foundation (no API calls yet)

### What this solves
v1 matches jobs to contractors with the Gemini API ([V1_PLAN.md](V1_PLAN.md)). This change builds everything a call needs, so the first real call can be checked before it is made:
- **Gemini client** (`src/jobmatch/llm/gemini.py`):
  - plain `httpx`, no new dependency; the key comes from `GEMINI_API_KEY` in `.env`;
  - temperature 0, JSON answers checked against a schema;
  - per-minute limits retried, the daily limit stops cleanly.
- **Usage log and budget guard** (`llm/usage.py`, `data/llm_usage.csv`):
  - every call is logged with its tokens, and the day counts from midnight Pacific time;
  - a run that would use most of today's allowance stops and says how many calls fit.
- **Smaller requests:**
  - the job text is pruned with what normalization already extracted (`llm/payload.py`): no employer pitch, benefits, company or pay;
  - contractors are sent as anonymized cards (`llm/anonymize.py`): no name, location, rate, hours, proof points or talent ID, and contact details are scrubbed from free text.
- **Prompt and answer checks** (`llm/prompt.py`, `llm/evidence.py`):
  - one verdict per contractor, and the best 5 described in detail;
  - quotes that are not in the text sent are removed and reported.
- **Contractor list:** optional workbook columns Primary Profession, Secondary Professions, Years Experience and Languages (`config/contractor_import.yaml`), for the prefilter in phase 3.
- **Config:** a new `llm:` section in `settings.yaml` (limits are GUESSES until copied from AI Studio), and `config/profession_titles.yaml`, the title phrases the phase 3 classifier will use.

### How to use it
- `docker compose run --rm app python -m jobmatch llm-preview` writes the requests for the first 3 active jobs to `data/llm_preview/<time>/`, plus the local alias map. Use `--job-id <id>` to pick jobs. **Nothing is sent.** Read the files before approving any API call.
- `... llm-preview --stats` prints token estimates for every active job, and how many requests and days a full run needs at the limits in `settings.yaml`.

### Known limits
- **No API call exists yet.** The first one is phase 4, after approval.
- **The v0 `match` and `run-once` commands are unchanged** until phase 6.
- **Scrubbing a name** removes every word equal to a part of it ("Grant", "Rose"), which can cost a skill word.

### Checks run
- `pytest`: 773 passed, 1 deselected (the model test). One existing test was updated for the four new `contractors.csv` columns.
- `ruff format` (8 new files rewrapped) and `ruff check .`: all pass.

---

## 2026-11-04 · v0 prototype complete: cleanup before v1

### What this solves
The v0 experiments are finished, and v1 will call a free external LLM API for the matching itself. The repo now keeps only what v0 runs on and what v1 needs to measure itself:
- **Removed:** nine experiment scripts (reranker, ColBERT and sparse modes, dual vectors, facets, combined fields, profession penalty, learned score). Their results stay in the docs.
- **`scripts/eval/` keeps the tools v1 needs:**
  - `label_sheet.py` makes and imports blind label sheets; its sampling no longer depends on the rejected methods;
  - `common.py` has today's score matrix and the ranking measures, with the blind consensus labels as the default;
  - `tune.py` re-checks the weights.
- **Removed the unused facet design:** the `Contractor` and `ContractorFacet` models, the `ContractorRepository` protocol, `CsvContractorRepository`, the `contractors.csv` and `contractor_facets.csv` tables in `CsvStore`, and the facet cross-check in `validate-data`. The contractor list is `data/contractors/contractors.csv` (`ContractorProfile`), as before. The tests for the general CSV rules (read-only tables, file-and-row errors, no cell values in messages, duplicate keys, blank lines, Excel BOM and leading zeros) now use a test-only read-only table, so those rules stay covered.
- **Docs:**
  - [PROTOTYPES.md](PROTOTYPES.md) is now a one-page summary; the full log is in `doc/archive/`;
  - README and [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) (new section 14) describe the v0 outcome and what v1 must decide (CLAUDE.md rules, privacy, rate limits, evaluation).

### How to use it
- **Blind labelling and evaluation:** same as before, with `-v ./scripts/eval:/exp:ro` instead of `scripts/prototypes`. See [LABELLING.md](LABELLING.md).
- **Comparing an LLM judge in v1:** build its jobs × contractors score matrix and use `Metrics`, `paired_bootstrap` and `cross_validate` from `scripts/eval/common.py`. Add its shortlist to `alternatives` in `label_sheet.py make` to sample where it and today's score disagree.

### Known limits
- **Still to do:** old exports and data backups (deleting them was blocked by a safety check; to be removed by hand).
- **`validate-data` no longer checks** `contractors.csv` or `contractor_facets.csv` in a data folder. The sample copies in `data/samples/` are left in place and ignored.
- **CLAUDE.md still states the v0 rules** ("No LLM in v0", Groq/Gemini off) and still names `ContractorRepository` and `contractor_facets.csv`. Rewrite it when v1 starts.

### Checks run
- `pytest`: 716 passed (one test fewer: the facet cross-check test was removed with the check).
- `ruff check .`, `ruff check scripts` and `ruff format --check`: all pass.

---

## 2026-11-04 · Vocabulary for other fields: contract wording, professions, industries, levels

### What this solves
The rules only knew how marketing, creative and tech postings are worded. A nursing, teaching, public-sector or trades posting could be misread: a "locum", an "LTO" or "specified period employment" wasn't recognised as a contract, and its profession note fell back to the nearest marketing or tech profession. Now:

**Contract wording** (`contract_phrases.yaml`), EN and FR:
- **Contract signals (48):**
  - locum and travel nursing;
  - LTO, sessional and chargé de cours;
  - specified period, période déterminée and term employment;
  - for the duration of the project;
  - temporary assignments;
  - Job Bank's "term or contract".
- **Permanent signals (7):** regular full-time, tenure-track, menant à la permanence.
- **False positives (18):** "general contractor" and "contract research organization" describe the employer, and "business casual" or "seasonal flu" are perks or health notes, not the job.

**Professions** (`professions.yaml`): 68 instead of 31, with 29 `close` pairs. The 37 new ones are grouped by NOC 2021 broad category: nursing, personal support, physicians, allied health, teaching, early childhood, social work, law, policy, public safety, IT support, cybersecurity, engineering, trades, driving, warehouse, manufacturing and more. Descriptions include common EN and FR title words.

**Industries:** 6 more:
- agriculture and food;
- mining, oil and gas;
- aerospace and defence;
- social and community services;
- public safety and emergency services;
- childcare and early learning.

**Levels:**
- apprentice and intern → junior;
- journeyperson / compagnon → intermediate;
- foreman / contremaître and charge nurse → lead;
- school principal → executive.

**Title abbreviations** for spotting duplicate postings: RN, RPN, LPN, PSW, ECE, HCA, CSR, EIT and others.

The skill list wasn't touched: it stays frozen (decision of 2026-11-02).

### How to use it
- Nothing new to run. New postings from these fields are read with the new wording.
- To extend a list, edit the YAML file and add a test case. For professions, run `embed-jobs` afterwards.

### Known limits
- **It doesn't create matches.** All 38 contractors are in marketing, creative, digital, product, finance, HR or sales.
- **Weights for the new contract phrases are guesses,** like the existing ones.
- **On bare titles,** "electrical engineer" and "line cook" stay "unclear". A real posting also has responsibilities, which help.

### Checks run
- 717 tests pass (new EN/FR cases for the contract phrases, levels, industries and abbreviations). Lint is clean.
- **Re-imported the jobs, then re-embedded and re-matched:**
  - no job's status, contract type, level or title changed;
  - every score is identical. The scoring version changed only because the industry list is part of it.
- **Profession notes:**
  - 2 jobs and 5 contractors got a different profession;
  - agreement with the 331 blind labels is unchanged ("different" covers 13 fits and 104 non-fits).
- **Typical titles from other fields:**
  - a first check on 47 titles found 11 misassigned, mostly French titles and titles missing from the descriptions;
  - after adding EN/FR title words to the descriptions, 22 re-checked titles give 19 right, 2 "unclear" and 1 on a close neighbour (sessional instructor → training and facilitation, now a `close` pair with post-secondary teaching).

---

## 2026-11-04 · Sheet 3 (Claude only) imported: the learned score doesn't hold up

### What this solves
Sheet 3 tested the learned score (option 3) on exactly the shortlist pairs where it differs from today's score. Claude labelled all 26; ChatGPT is pending.
- **The 8 pairs only the learned score shortlists:** no fits, 3 partial, 5 non-fits.
- **The 3 pairs only today's score shortlists:** all non-fits.

On all 331 labels, the learned score's held-out gain falls from +0.017 to +0.003, and its shortlists hold fewer fits than today's (76 vs 82).

**Not adopted. Today's score and the profession note stay as they are.**

### How to use it
- **When ChatGPT's labels arrive:** add them to the same file (`chatgpt_label`, `chatgpt_reasoning`) and run `label_sheet.py import` again. Claude's labels aren't added twice, and the consensus is rebuilt.
- **Then re-run** `learned_score.py`.

### Known limits
- **Until ChatGPT's labels are in,** sheet 3 counts on Claude's word alone (`ai-single` in the consensus).
- **Only 11 pairs decide the question.**

### Checks run
- Dry-run import, then the import itself (26 labels; the consensus was backed up first).
- `learned_score.py` re-run on the 331-pair consensus.

---

## 2026-11-04 · Label sheet 3: testing the learned score's shortlists

### What this solves
The learned score (option 3) couldn't be judged on its shortlists, because it brings in pairs nobody has labelled. Sheet 3 holds exactly those pairs:
- **8** only the learned score shortlists;
- **3** only today's score shortlists;
- **15** both shortlist, as a reference.

Once labelled, its shortlists can be compared with today's directly.

The model trained on all 305 labels changes very few unlabelled shortlist pairs. Most of its differences from today's score are among pairs already labelled, so the sheet is short.

### How to use it
- **Give the judges** `exports/label-sheet-20261104T040703Z.csv` (26 pairs, no names) with the instruction text in [LABELLING.md](LABELLING.md).
- **Settle disagreements:** fill `human_label` for the rows where the AIs disagree.
- **Import it,** then re-run `learned_score.py`.
- **To make another sheet like it:** `learned_score.py --sheet`. `label_sheet.py` now has a shared `write_sheet` function, and `make` works as before.

### Known limits
- **Only 11 pairs decide the question.** With the 15 reference pairs it's a quick round, but a small one.
- **The shortlists reflect today's jobs and contractors.**

### Checks run
- The sheet has no names and no pair labelled before. A dry-run import reads its columns.
- Script lint and format are clean.

---

## 2026-11-04 · Prototype: a score learned from the blind labels (option 3)

### What this solves
Today's score averages five parts with hand-picked weights, and it can't use evidence that should count against a match, such as a different profession or being over-qualified. The prototype learns the combination from the 305 blind labels, using the five parts plus the profession verdict, the seniority gap, missing must-haves and the industry.

The best version beats today's score on 70% of held-out job folds (mean +0.017). That's the best result so far: re-fitting the weights alone gave 61%.

**Nothing in matching changed.**

### How to use it
- **Run it:** `docker compose run --rm -e LABELS=data/eval/blind_consensus.csv -v ./scripts/prototypes:/exp:ro app python /exp/learned_score.py` (about 2 minutes, read-only).
- **Results and next step:** in [PROTOTYPES.md](PROTOTYPES.md).

### Known limits
- **Intervals touch zero.**
- **Its shortlists can't be judged yet:** it brings in about 9 unlabelled pairs.
- **A few learned weights are likely artefacts** of how the label sheets were sampled.
- **Needs one more small label sheet** before it can be considered for adoption.

### Checks run
- Cross-validation by job, 20 × 5 folds, with the L2 strength chosen inside each training fold.
- Script lint is clean.

---

## 2026-11-04 · Label sheet 2 imported; everything re-checked on 305 blind pairs

### What this solves
Sheet 1's 40 fits were too few to settle anything. With sheet 2, the blind labels cover **305 pairs: 98 fit, 170 no, 37 partial**, including the 11 contractors added on 2026-11-03.

Re-checked on all of them:
- **Today's settings hold:** within one job, fits rank above non-fits 87% of the time; across jobs, 90%.
- **Bands:** a fit rate of 88% in high, 75% in medium, 29% in low and 4% below `min_score`. Keep `min_score` at 0.45; 0.60 would lose 30 fits.
- **The profession note:** 89% of the labelled pairs marked "different" are rejections.
- **Two leads from sheet 1 vanished:** the raw-text vector and strength × industry sentences.

**Nothing in matching changed.**

### How to use it
- **The fixed copy:** ChatGPT's answers were one column to the left and its text had broken characters. The corrected copy, `exports/label-sheet-20261104T021127Z_labeled_fixed.csv`, was imported; the original is untouched.
- **New consensus rule:** one AI's "fit" with the other's "partial" counts as fit. The change is in `label_sheet.py`; see [LABELLING.md](LABELLING.md).
- **Results:** in [PROTOTYPES.md](PROTOTYPES.md).

### Known limits
- **No person labelled sheet 2.** It's AI judges only, and the 37 pairs where they disagree count as partial.
- **Sheet 2's "fit" is broader than sheet 1's,** because it includes Claude's partials.
- **Re-fitted weights won slightly on held-out jobs for the first time** (+0.006, in 61% of folds). Not adopted; re-check after the next sheet.

### Checks run
- Dry-run import, then the import itself (300 labels; the consensus was backed up first).
- `tune.py`, `professions.py`, `more_combos.py` and `dual_embedding.py` were run on the 305-pair consensus.
- Script lint is clean.

---

## 2026-11-04 · Profession note for reviewers; label sheet 2

### What this solves
- **Reviewers couldn't see a profession mismatch.** The most common reason the blind judges rejected a match was "same domain, different profession": recruiting vs sales, UX writing vs UX design, HR vs operations. Each shortlisted pair now carries a hint, for example `job: recruiting and talent acquisition · talent: sales and business development (different)`.
  - **It changes no score or shortlist.**
  - **How reliable it is:** on the 155 blind labels, "different" covered 2 fits and 53 non-fits.
- **The 11 contractors added on 2026-11-03 had no blind labels,** and the open questions needed more evidence. Label sheet 2 has 150 new pairs: 82 involve those contractors, and 49 are pairs where today's open candidates disagree.

### How to use it
- **Where the note appears:**
  - the `profession` column in `exports/trial-run-<time>.csv` and in the notebook;
  - `exports/matches-<time>.csv`;
  - a "profession (hint)" line in the `match` output.
- **How to read it:**
  - "different": look twice.
  - "close": a neighbouring profession the judges often accept.
  - "unclear": the posting's profession couldn't be told.
- **Changing the profession list:** edit `config/professions.yaml` (descriptions, `close` pairs, `min_margin`), then run `embed-jobs` again.
- **Label sheet 2:** `exports/label-sheet-20261104T021127Z.csv`. It has **no contractor names**, so it can be pasted into ChatGPT or Claude as it is. Give them the fixed instructions in [LABELLING.md](LABELLING.md). Then decide yourself the rows where the two AIs disagree, in `human_label`. Import the filled sheet with `label_sheet.py import`.

### Known limits
- **The profession list and the `close` pairs are a first guess.** Review them.
- **10 of 82 jobs get "unclear".**
- **`embed-jobs` builds two more vector sets,** about 6 s here, only for new or changed jobs.

### Checks run
- 675 tests pass (new: profession config, assignment, verdicts, notes end to end, stale vectors, the job core text in EN and FR, the export column). Lint and format are clean for `src`, `tests` and `scripts`.
- `embed-jobs`, `match` and `export-trial-run` ran. The scoring version is unchanged (`437f851e6100`), and no rows were re-stored.
- **Sheet 2 checked:** 150 pairs, no contractor names, no pair from sheet 1. A dry-run import reads its columns.

---

## 2026-11-04 · Prototype: profession check

### What this solves
Most matches the judges rejected were the same domain but a different profession: an HR manager job offered to an operations director, recruiting to a salesperson, UX writing to a UX designer. The matcher's text similarity can't see the difference.

This prototype names each job's and each contractor's profession from a short list, and checks it against the blind labels:
- **Agreement:** 81% of rejected pairs are a different profession, and 78% of fits are the same.
- **As a score penalty:** it cleans the shortlists, but it isn't reliably better at ranking across jobs.

Nothing in matching changed.

### How to use it
- **Review the draft profession list** in `config/professions.yaml`. It's read only by the prototype.
- **Run the prototype:** `docker compose run --rm -e LABELS=data/eval/blind_consensus.csv -v ./scripts/prototypes:/exp:ro app python /exp/professions.py` (about 1 minute). It prints each job's and each contractor's profession, so the list can be checked.

### Known limits
- **Adjacent professions are counted as mismatches.** Creative direction, graphic design and copywriting accept each other, and the list doesn't say so yet.
- **Generic contractor titles** ("consultant", "advisor") are assigned badly.
- **Small sample:** 155 labelled pairs.

### Checks run
- The prototype ran on the blind labels with cross-validation. Results are in [PROTOTYPES.md](PROTOTYPES.md).
- Lint is clean.

---

## 2026-11-04 · Blind labels imported; matcher re-checked against them

### What this solves
Until now, every setting was tuned on Claude Code's own labels, which weren't blind. The returned sheet now gives labels made without seeing any score: 13 by a person, and all 155 by ChatGPT and Claude (web), each with reasons. The person agreed with both AIs on all 13 pairs.

The matcher was re-checked against these labels:
- **Today's settings hold up:** within one job, fits rank above non-fits 92% of the time; across jobs, 93%.
- **Re-fitting the weights didn't beat today's** on jobs held out from the fit.
- **Nothing in the scoring changed.**

### How to use it
- `label_sheet.py import` now reads the judge columns (`human label`, `ChatGPT Label`, `Claude Label` and their reasons). It writes `data/eval/blind_consensus.csv`: one label per pair, human first, then the AIs where they agree, else partial.
- **Re-tune after the next sheet:** `docker compose run --rm -e LABELS=data/eval/blind_consensus.csv -v ./scripts/prototypes:/exp:ro app python /exp/tune.py`.
- **Reading the bands:**
  - **Medium and high** (score 0.60 and up) are fits about 8 times in 10.
  - **Low** (0.45–0.60) is a fit about 1 time in 10.

### Known limits
- **155 pairs, 40 fits, 27 jobs with a fit.** Small, and the sheet over-samples hard pairs.
- **The 11 contractors added on 2026-11-03 aren't on the sheet.** The next sheet should include them.
- **Two candidates wait for a decision:**
  - the raw-text vector, better on both label sets but with a few more non-fits in shortlists;
  - a profession check: most high-scored non-fits are the same domain but a different profession.

### Checks run
- Import dry run, then the import itself: 323 labels.
- `tune.py`: 432 weight combinations with cross-validation.
- All five prototype comparisons re-run on the blind labels.
- Tests and lint pass.

---

## 2026-11-03 · One command for contractor updates; jobs re-tagged with new skills

### What this solves
- **New profiles meant remembering six steps in the right order,** and in particular re-importing jobs when the build learned new skills. Without that step, jobs didn't get the new skill tags. `scripts/update_contractors.sh` runs all six. It re-imports the jobs only when `skills_learned.yaml` actually changed.
- **Jobs now carry the skills learned from the 11 added profiles.** 8 jobs gained tags (mostly "b2b"). No job changed status, and 52 of 82 still have matches.

### How to use it
```
bash scripts/update_contractors.sh --dry-run   # shows the steps
bash scripts/update_contractors.sh             # default workbook: data/contractors/contractor-master-list.xlsx
bash scripts/update_contractors.sh path/to/book.xlsx
```
Don't run it while an import or a match is running.

### Known limits
- **A re-import moves `jobs.csv` and `runs.csv` into `data/backups/reset-<time>/`.** It re-reads every posting from `data/inbox/` and the page cache. Links that never fetched may be tried again.
- **`ruff check .` in the container doesn't see `scripts/`,** because the folder isn't mounted. Lint scripts with `docker compose run --rm -v ./scripts:/app/scripts:ro app ruff check scripts`.

### Checks run
- Dry-run, `--help` and a bad option all behave as described.
- The re-import, embed, match and export steps were run by hand: same 82 jobs, same statuses.
- 660 tests pass. Lint and format are clean for `src`, `tests` and `scripts`.
- Prototype results are in [PROTOTYPES.md](PROTOTYPES.md): seven more field combinations, none adopted.

---

## 2026-11-03 · Search Keywords used in matching; 11 more contractors

### What this solves
- **The workbook's Search Keywords column was ignored.** It now feeds the contractor's embedding text (a `Keywords:` line after the skills), the skill detection and the word overlap. It's also a new `search_keywords` column in `contractors.csv`, and the notebook shows it.
- **5 profiles weren't joined to their Talent row** because their IDs read `TAL-014` instead of `TAL-ON-014`. The IDs were fixed in the workbook. With 6 new talent rows, the list grows from 27 to 38 contractors. Talents with no profile row are still left out (10), and so is TAL-BC-005, which has no Talent row.

### How to use it
- Nothing new to run. Fill in Search Keywords in the workbook, separated by `;`, then `build-contractors`, `embed-contractors`, `embed-jobs`, `match`.
- Older workbooks without the column still build.

### Known limits
- **On Claude's labels, the keywords make ranking slightly worse** (fit-or-partial vs no 0.814 → 0.784; the interval excludes zero). See [PROTOTYPES.md](PROTOTYPES.md). They stay in until blind labels decide.
- **The template version changed,** so every vector was rebuilt and every score moved.
- **The 11 added contractors are not in the blind label sheet.** Make a new sheet once they've been reviewed.
- **The rebuild learned new skill phrases from the new profiles.** Jobs get those tags only after `bash scripts/reset_reimport.sh` (not run yet).

### Checks run
- 660 tests pass (new: keywords parsed from the workbook, an older workbook without the column, the `Keywords:` line, keywords in the word overlap). Lint and format are clean.
- `build-contractors` and `validate-data`: 38 contractors, all data valid. Re-embedded 38 contractors and 82 jobs; `match`: 52 jobs matched, 30 with no match. Trial-run export written.

---

## 2026-11-03 · Blind labelling sheet for reviewers

### What this solves
Every tuning decision so far rests on Claude's labels. They aren't blind, and the open questions (sparse word weights, bge-m3 mix, raw-text vector, dropping the skill list, the bands) are too close to call on them. A reviewer can now label pairs **without seeing any score**, and those labels decide.

### How to use it
- **Give the reviewer two things:** `exports/label-sheet-20261103T015024Z.csv` (155 pairs, 62 jobs) and [LABELLING.md](LABELLING.md). Don't give them the key in `data/eval/label-sheets/`.
- **When the sheet comes back,** import it with `scripts/prototypes/label_sheet.py import <sheet> [--dry-run]`. Labels go to `data/eval/blind_labels.csv` (append-only). The import also reports agreement with Claude's labels.
- **Re-run any comparison on the blind labels** by setting `LABELS=data/eval/blind_labels.csv`.

### Known limits
- **The sheet holds contractor names.** Keep it local.
- **One reviewer gives one opinion.** Two reviewers on the same sheet would also show how much reviewers agree with each other.

### Checks run
- **Self-test:** a copy filled with Claude's labels imported in dry-run mode with 100% agreement (109 shared pairs). A wrong label was rejected. Nothing was written.
- Lint is clean.

---

## 2026-11-03 · Long postings are no longer cut; bge-m3 output modes tested

### What this solves
- **Long postings were cut off.** The embedding model read only the first 512 tokens, and 35% of job texts are longer, so the end of their requirements was ignored. It now reads 1,024 tokens, which covers every posting we have, for about 15% more embedding time.
- **The token limit is now part of the stored model version.** Before, changing it would have silently reused the old vectors. Now it re-embeds everything.
- **Tested and not adopted** (details in [PROTOTYPES.md](PROTOTYPES.md)):
  - a second, raw-text vector;
  - a context longer than 2,048 tokens (no posting is that long);
  - `bge-m3`'s sparse and ColBERT outputs.

### How to use it
- After pulling, re-embed once:
  ```
  docker compose run --rm app python -m jobmatch embed-contractors
  docker compose run --rm app python -m jobmatch embed-jobs
  docker compose run --rm app python -m jobmatch match
  ```
- On the client Mac, expect a few minutes for the first re-embed.

### Known limits
- **No measurable change in the shortlists** on Claude's labels: the same 55 fits and 48 non-fits. The change is about not losing text.
- **The most promising untested swap is `bge-m3`'s sparse output in place of TF-IDF.** Its result is mixed, and it waits for blind labels.

### Checks run
- **Tests:** 659 pass, including a new test that a different token limit gives a different model version. Lint is clean.
- **Real data:** all 27 contractors, 174 titles and 82 jobs re-embedded; matched, exported, and 2 new pairs labelled.

---

## 2026-11-03 · Word overlap in the score, skill list frozen, clearer bands

### What this solves
- **Ranking depends less on the hand-made skill list.** A new score part, TF-IDF word overlap, compares the raw posting with the whole profile. Every word counts, and rare words count more. There are no aliases to maintain, and it works the same in English and French.
  - The skill list is **frozen for ranking**: add or fix entries only when a wrong tag misleads a reviewer.
  - The skill list still drives the "matched skills" and "missing must-haves" shown to reviewers.
- **The bands mean more.** "Medium" now starts at 0.60 instead of 0.50. On judged pairs: high is 82% fit, medium 41% (was 19%), low 5%.
- **The reranker test model is removed** (2.3 GB freed), and so is its code.

### How to use it
- Nothing new to run. After pulling:
  ```
  docker compose run --rm app python -m jobmatch match
  docker compose run --rm app python -m jobmatch export-trial-run
  ```
- `match` output now shows `words 0.xxx` beside text, title and skills.
- **After every import,** the next `match` stores all shortlists again (about 200 rows), because word rarity changed. That's expected.

### Known limits
- **The gains are small and within the noise of Claude's labels.** It's slightly better at separating fits from non-fits, and slightly worse at putting a fit above a partial. Blind reviewer labels are still the next step.
- **One reviewer A pair now reads "low"** (still shortlisted).
- **The word-overlap value isn't stored in `matches.csv`;** adding a column would need a migration.
- **Settings are guesses:** `weights.lexical` 0.2, `lexical_ceiling` 0.10 and `bands.medium` 0.60.

### Checks run
- **Tests:** 658 pass, with new tests for tokenizing (EN/FR), word rarity, the fingerprint and the score part. Lint and format are clean.
- **Real data:** 82 jobs × 27 contractors. The same 55 fits are shortlisted, with 6 fewer non-fits. 9 new pairs were labelled. Details are in [PROTOTYPES.md](PROTOTYPES.md).

---

## 2026-11-02 · Prototypes: can we match without growing hand-written rules?

### What this solves
Every new batch of postings needed new skill aliases and parser patches. We tested three alternatives against 381 labelled pairs: sentence matching, a cross-encoder reranker, and TF-IDF word overlap. Full results and recommendations are in [PROTOTYPES.md](PROTOTYPES.md). In short:
- **Keep the structural parser.** Without its cleanup, quality drops clearly.
- **The skill list isn't needed for ranking.** TF-IDF word overlap beats it on every measure, with no upkeep. Keep the skill list for explanations only.
- **The reranker and sentence matching don't pay off.** The reranker is also too slow for the client's Mac.

### How to use it
- Re-run the comparison (read-only):
  ```
  docker compose run --rm -v ./scripts/prototypes:/exp:ro app python /exp/compare_methods.py
  ```
- Nothing in the pipeline changed.

### Known limits
- **The labels are Claude's, not blind.** Differences under about 0.05 AUC are noise. Blind reviewer labels are the next step.
- **The reranker model** (about 2.3 GB) is in the `hf-cache` Docker volume from this test.

### Checks run
- 123 more pairs labelled, so the parser-free methods' own picks are judged too.
- 90% bootstrap intervals over jobs.
- Lint is clean.

---

## 2026-11-02 · 34 new postings checked; thin profiles out of matching; fixes from the results

### What this solves
- **34 new postings imported and checked.** Every one is a contract by its own wording. Six are doubtful:
  - Jelly SEO says "Contract" but offers a salary with bonuses.
  - Five Job Bank postings (Kitchener Housing, City of Calgary, three PCL) say only "Term or contract".

  They stay in, because the tool keeps anything unclear for review. Reviewers should confirm them. About 10 others are fixed-term salaried jobs (union, benefits), not independent-contractor work.
- **Contractors with no profile text are left out.** The 15 contractors with no Executive Summary and no Core Strengths (TAL-ON-014 to TAL-ON-031) were matched on their headline alone. They are now skipped at `build-contractors` (`require_profile_text` in `config/contractor_import.yaml`), and their vectors are dropped. The pool is 27 contractors. **Filling in their profile in the workbook brings them back on the next build.**
- **Fewer wrong high-band matches:**
  - **Data Strategist (City of Calgary)** had no skill tags and matched a Social Media Strategist in the **high** band on the word "strategist". New skills (data visualization, workforce planning, forecasting) and "Power Business Intelligence" as Power BI now tag it.
  - **"Fractional" job titles matched every fractional executive.** "Fractional", "interim" and "freelance" are now removed from titles before they are compared (`title_engagement_words` in `config/normalization.yaml`). Fractional CFO for a Fractional HR Manager went from high to medium.
  - **"Creative Director" counted as executive,** so a Senior Creative Director was flagged as under-qualified. Creative, art and design director titles (EN/FR) are now the **lead** level. "Executive creative director" and bare "Director" stay executive.
- **Wrong skill tags removed:**
  - "participant recruitment" and "recruitment campaigns" were tagged as HR recruitment.
  - "defect triage" was tagged as patient care.
  - A new **influencer marketing** skill tags influencer roles.
- **Fields read better:**
  - **Contract length:** "3 months probationary period" is no longer the contract length. "2-year temporary full-time contract" and "one-year parental leave contract" are read.
  - **Pay:** a date after the pay ("Advertised until 2026-11-15") is no longer read as pay.
  - **Hours:** "37.5-hour work week" counts as full-time.
- **Stricter shortlist:** `min_score` went from 0.40 to 0.45. In 168 judged pairs, nothing between 0.40 and 0.45 was a fit.
- **Exports no longer show stale shortlists:**
  - A job that the newest `match` left without a shortlist kept showing its old shortlist in `export-matches` and `export-trial-run`. Both exports now use the newest scoring version only, and only for jobs still marked `matched`.
  - Contractors no longer in the list are left out of both exports.

### How to use it
- After pulling, run these in order:
  ```
  docker compose run --rm app python -m jobmatch build-contractors data/contractors/contractors_master_list.xlsx
  bash scripts/reset_reimport.sh -y
  docker compose run --rm app python -m jobmatch embed-contractors
  docker compose run --rm app python -m jobmatch embed-jobs
  docker compose run --rm app python -m jobmatch match
  docker compose run --rm app python -m jobmatch export-trial-run
  ```
- **To bring a contractor back:** fill in their Executive Summary or Core Strengths in the workbook, then rebuild.

### Results (82 jobs × 27 contractors; labels are Claude's, not blind)
| Band | Before: % fit | After: % fit | After: fit / partial / no |
|---|---|---|---|
| high | 86% | 89% | 32 / 4 / 0 |
| medium | 21% | 19% | 22 / 62 / 32 |
| low | 2% | 3% | 1 / 15 / 22 |

- Precision is 0.29 (0.50 counting half of partials). It was 0.27 (0.48) for the same contractors before.
- No previously shortlisted fit was lost.
- Reviewer A/B pairs are unchanged: 5 of 6 are shortlisted.
- `data/eval/llm_judge_labels.csv` now has 294 labelled pairs (169 added today, no duplicates). Every current shortlist pair is labelled.

### Known limits
- **The medium band is mostly noise.** Below a score of 0.60 only 5 of 104 shortlisted pairs are fits. From 0.60 to 0.70 it's 35–38%, and from 0.70 it's 75–96%.
- **Many new postings have no counterpart in our pool:** business analysts, controllers, construction, compensation, HSE.
- **Agency postings mention their client's departments,** so "Senior Graphic Designer" is still tagged sales enablement, product marketing and Salesforce.
- **A rebrand phase's "3–6 months" is still read as a 6-month contract** on that posting. Duration is information only.

### Checks run
- **Tests:** 649 pass, with new EN/FR cases for every rule above. Lint and format are clean.
- **Real data:** reset and re-import with no network calls, then re-embed (27 contractors, 174 contractor titles, 82 jobs), match, export and evaluate.

---

## 2026-10-28 · Trial-run export in the pipeline, quick start guide and new README

### What this solves
- **The notebook's input is now made by a command.** `data_visualizer.ipynb` reads `exports/trial-run-<time>.csv`. Until now, that file was written by hand outside the code. `export-trial-run` now writes it from the latest matching run:
  - the best shortlisted contractors per active job (2 by default; flagged matches are left out);
  - one "no match" row for a job with no shortlist;
  - the same columns as before, so the notebook needs no change.
- **[quick_start.md](quick_start.md):** every command in one place, plus the steps to import jobs, reset and re-import, run the model, find the results and read them in the notebook.
- **README.md** is now a standard project README: what the repo does, how it works, setup, usage, structure and a map of the docs.
- **Docs moved to `doc/`:** `DEVELOPMENT.md`, `LOG-UPDATE.md` (this file), `PROJECT_CONTEXT.md`, `quick_start.md`, the pipeline diagram PNG and the project PDF. `README.md` and `CLAUDE.md` stay at the root (Claude Code only loads `CLAUDE.md` from there), as does `data/samples/README.md`. File names mentioned in older entries below refer to the old root paths.

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
