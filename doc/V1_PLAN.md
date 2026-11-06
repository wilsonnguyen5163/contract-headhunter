# V1 plan: matching with the Gemini API

Status (2026-11-05): phases 0, 1 and 2 done; phase 4 started with 2 real calls (profession
suggestions for all 38 contractors, one matching call). Phase 2 measured 82 active jobs at
about 9.5k tokens a call, 16% of the daily requests, so the prefilter (phase 3) is not needed
for the budget yet. Each phase
needs a cost estimate and approval before work starts (CLAUDE.md rule 3), and every command
or API call needs approval before it runs (rule 2).

Built differently from the first draft:
- Title phrases live in a new `config/profession_titles.yaml`, not as a `titles:` list in
  `professions.yaml`. The v0 loader for that file stays untouched.
- The contractor fields are added as optional columns of the existing `ContractorProfile`
  rather than a separate `ContractorProfileV1` model. Older lists still load.
- `llm-preview --stats` (phase 2's tool) was built with phase 1. Phase 2 is now just running it.

Numbers marked GUESS are estimates to be replaced by measurements.

## 1. Goal and ground rules

v1 replaces the v0 matcher (embeddings + weighted score) with the Gemini API. Given a job,
Gemini judges which contractors fit and explains why, with evidence quoted from the text it
was given. A person still makes every final decision.

Fixed by CLAUDE.md:
- **Gemini only.** No other LLM provider, no paid tier.
- **v0 matcher is detached.** v1 never runs or imports `embed/` or `match/` (scorer, gates,
  lexical, professions, fields). Normalization (`normalize/`), intake (`sources/`, `ingest.py`)
  and storage stay; they are intake, not matching.
- **No personal details leave the machine.** Contractor names, addresses, phone numbers (and,
  by this plan, emails, URLs, location, rate, hours and talent IDs) are never sent.
- **Every call reports its token usage**, and the run warns before a call or batch would use
  up the day's allowance.

## 2. Decisions taken (2026-11-04)

Accepted with the first draft of this plan. Change them here if that is wrong.

| # | Decision | Choice |
|---|---|---|
| D1 | Contractor data sent to Gemini | Structured fields plus a scrubbed short summary. **No proof points** |
| D2 | v0 code | Stays in the repo, unused and not imported by v1 |
| D3 | Seniority, rate and profession mismatch | Gemini reports them as red flags; no v0 checks |
| D4 | HTTP client | Plain `httpx` against the REST API; no Google SDK, no new dependency |
| D5 | Matches storage | New `data/llm_matches.csv`; v0's `matches.csv` is left as it is |

Open decisions are in section 11.

## 3. How the Gemini API works (what to expect)

- **One request, one answer.** `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`
  with the key in the `x-goog-api-key` header. The model name and URL live in
  `config/settings.yaml`; the key lives in `.env` as `GEMINI_API_KEY`.
- **Structured output.** The request carries a JSON schema (`responseMimeType:
  application/json`), and the answer is JSON in that shape. We still validate it with pydantic
  and reject anything that doesn't fit.
- **Token usage comes back with every answer** in `usageMetadata`: input
  (`promptTokenCount`), output (`candidatesTokenCount`), thinking (`thoughtsTokenCount`) and
  total. Thinking tokens count against the quota, so thinking is set low or off in config.
- **Limits are per project and per model:** requests per minute (RPM), tokens per minute
  (TPM) and requests per day (RPD). Going over a per-minute limit returns HTTP 429 at once;
  going over the daily limit blocks the model until midnight Pacific time.
- **The published free-tier numbers disagree and keep shrinking** (reports range from about
  20 to 1,500 RPD for Flash models; Pro is mostly gone from the free tier). **The real numbers
  for our project are on the AI Studio rate-limit page.** They go into config; code never
  assumes them.
- **Data terms (unpaid tier).** Google may use inputs and outputs to improve its products,
  human reviewers may read them, and the terms ask users not to send personal information.
  That is why contractor data is anonymized and trimmed (sections 6 and 8), and why resumes
  are scrubbed locally before any intake call (section 9).

## 4. Pipeline in v1

```
links / pasted postings ─► fetch ─► normalize ─► dedupe & filter ─► data/jobs.csv
                                       │
                                       └─► job profession + field   (section 7)
                                                 │
contractor intake (section 9) ─► contractors.csv (profession, field, skills…)
                                                 │
                                                 ▼
                                    PREFILTER: contractors in the job's
                                    profession or a close one   (no API)
                                                 │
                                                 ▼
                         build payload: pruned job + anonymized short roster
                                                 │
                         budget check (today's usage vs limits) ─► stop and warn if short
                                                 │
                                                 ▼
                                Gemini: verdict per contractor, top-k detailed
                                                 │
                     validate JSON · check evidence quotes · log tokens
                                                 │
                                                 ▼
                         data/llm_matches.csv ─► exports ─► human review
```

## 5. Token and request budget

### Where the limit bites

- **Requests per day (RPD) is the real limit**, not tokens. At about 10k tokens per call,
  250k TPM allows about 25 calls a minute, while RPM is about 10–15.
- **Pruning and the prefilter lower tokens per call.** They lower requests only when:
  - a job with no eligible contractor is skipped (no call at all), or
  - the smaller rosters let us put several jobs in one call (section 5.3).

### Per call estimates

All values are GUESSES. Measured base: the 38-contractor `contractors.csv` is 29 KB, about
7k tokens.

| Setup | Input tokens | Output tokens | Requests per job |
|---|---|---|---|
| A. Full roster, raw job text | 9–11k | 1–1.5k | 1 |
| B. Full roster, pruned job (section 6) | 7–9k | 1–1.5k | 1 |
| C. Prefiltered roster (5–12 contractors), pruned job | 2.5–4.5k | 0.5–1k | 1 |
| D. Like C, 5 jobs of one profession per call | 6–10k | 2–4k | 0.2 |
| Job with no eligible contractor | 0 | 0 | 0 |

### How long a backlog takes (requests = jobs ÷ jobs per call)

| Free-tier RPD | 300 jobs, setup B or C | 300 jobs, setup D |
|---|---|---|
| 20 | 15 days | 3 days |
| 250 | 2 days | 1 day |
| 1,000+ | 1 day | 1 day |

Daily new postings (probably tens) fit setup C at any RPD of about 100 or more. Setup D is
only needed if our real RPD is low, because batching can lower answer quality and one failed
call loses several jobs. It stays a config switch (`llm.jobs_per_call`, default 1).

### Budget guard (required by CLAUDE.md)

- **Usage log.** Every call is appended to `data/llm_usage.csv`: time, model, job IDs,
  input, output and thinking tokens, total, status and HTTP code. The terminal prints one
  line per call and a run summary.
- **Before a run:** estimate the calls and tokens needed (local count, about 4 characters
  per token, GUESS) and compare them with what is left of today's RPD and TPD (from the log,
  day boundary at midnight Pacific).
  - If the run would use **more than 80% (GUESS) of what is left**, it stops and lists how
    many jobs fit.
  - `--yes` goes ahead, or `--max-calls N` caps the run.
- **Before each call:** flag any single call estimated above **50% of TPM** (GUESS), for
  example a huge posting or roster. It is skipped and reported instead of sent.
- **During the run:** wait `60 / RPM` seconds between calls. On a per-minute 429, back off
  and retry (at most 3 tries). On a daily-limit 429, stop cleanly; the next run picks up
  where this one stopped.
- **Re-runs cost nothing.** A job is skipped when it was already judged with the same model,
  prompt version, job payload hash and roster hash.

## 6. Pruning the job payload with normalization

Normalization already splits each posting into labelled parts. The payload sends only what
decides fit, built by new code in `llm/payload.py`. It does not reuse `embed/texts.py`,
which belongs to v0.

**Send:**
- Title (`title_norm`), seniority, `years_experience_min`.
- Responsibilities, then requirements, then nice-to-have. Normalization already caps each
  at 20 items, and duplicate bullets are dropped.
- Contract facts in one line: employment type, duration, commitment, work mode, province.
- Language (EN/FR). French postings are sent in French; Gemini reads both.

**Drop:**
- `description_raw`, and any employer pitch, benefits, EEO and how-to-apply text
  (normalization already removes these from `description_clean`).
- Company name and URL, which don't help the judgment. Pay too, for now: contractor rates
  aren't sent, so pay can't be compared (see open question Q4).

**Fallback:** a posting with no lists sends its `description_clean`, cut at a token cap.
`llm.max_job_tokens` is GUESS 1,200. Measured on v0 data: median 440 tokens, longest 948.

**Contractors are trimmed the same way:**
- headline, primary and secondary profession, field or industry, seniority, years of
  experience;
- target titles, core skills, search keywords;
- a short summary of at most 80 words (GUESS).

`llm-preview` shows the token count of each part so pruning can be checked before any call.

## 7. Prefilter: profession and field before Gemini

### Idea

For each job, send Gemini only the contractors whose profession is the job's or a close
one. This cuts tokens per call, skips jobs nobody fits, and makes setup D possible.

### Vocabulary

- **Professions:** the list in `config/professions.yaml` (what a person is accountable for,
  with `close` groups).
- **Fields:** `config/industries.yaml`.

These are YAML data, not v0 code. v1 reads them with new code; the v0 embedding classifier
(`match/professions.py`) is not used. The same lists become the dropdowns in the contractor
intake form (section 9), so both sides share one vocabulary.

### How a job gets its profession (no v0 code)

1. **Rules first, no API.** New `llm/classify.py` matches the cleaned job title against
   title patterns for each profession. The patterns are a new `titles:` list per entry in
   `professions.yaml`.
2. **Batched Gemini call for the rest.** Jobs the rules can't place go to one call with
   about 30–50 jobs: title and first 3 responsibilities each, about 100 tokens per job.
   The answer is a profession from the list, or `unclear`. One request covers about 40
   jobs, so this costs little of the daily quota.
3. **`unclear` means no prefilter.** That job gets the full roster.

### How a contractor gets a profession

- From the intake form (section 9). For the current 38 contractors, a person fills in a new
  column in the master workbook. That takes about 30 minutes, uses no API, and gives us
  ground truth.

### Rules (all in config)

- **Eligible contractors:** same profession, or a profession in the same `close` group, as
  either the contractor's primary or a secondary profession.
- **Field is a soft signal, not a filter.** Reviewers rated a cleantech marketer A for a
  sports role. Field is sent to Gemini as context only.
- **Minimum roster:** if fewer than `llm.prefilter.min_roster` contractors remain (GUESS 3),
  add contractors from the close group, then the full roster. This protects recall.
- **No eligible contractor at all:** status `no_match`, reason `no_profession_match`, no
  call. Listed in the run report so a person can override.
- **Explanations:** every excluded contractor is logged with the reason, as counts plus talent
  IDs, never names.

### Risk and check

The prefilter is the main recall risk, because a fit outside the job's profession is never
seen by Gemini.
- **Evidence from v0:** a "different profession" verdict hit 2 fits against 53 non-fits on
  155 blind labels, and 10 of 82 jobs were unclear. That suggests a small loss, but v1's
  classifier is new and must be measured again.
- **Gate before switching it on:** measure it offline against the blind labels in
  `data/eval/blind_consensus.csv`, with no API calls. Count how many labelled fits the
  prefilter would exclude.
  - Target (GUESS): at most 2% of fits lost.
  - Until it passes, `llm.prefilter.enabled: false` and every job gets the full roster
    (setup B).

## 8. Anonymizing contractor data

Built in `llm/anonymize.py`, with unit tests on English and French examples.

- **Never sent:**
  - name, location or address, phone, email, URLs, rate, weekly hours;
  - talent ID and proof points (D1).
- **Aliases:** each contractor gets an alias for the call (`C01`, `C02`…). The map back to
  the talent ID stays in memory and in `llm_matches.csv`. It is never sent.
- **Scrub before sending:** free text (the short summary) loses the contractor's own name
  (each part, accent-insensitive), emails, phone numbers, URLs and Canadian postal codes.
  If something that looks like contact data remains after scrubbing, that contractor is left
  out of the call and reported.
- **`llm-preview`** writes the exact request bodies to
  `data/llm_preview/<time>/` for a person to read, without calling the API.

## 9. Contractor intake automation (separate workstream)

### Plan

Contractors upload a resume and fill in a form. An LLM summarizes the resume into the
structured row the matcher needs. This feeds the prefilter and makes every contractor
row look the same.

### Shared contract

**A versioned contractor schema** (`ContractorProfileV1` in `schemas.py`), agreed before
either side is built:

| Field | Source | Sent to Gemini for matching |
|---|---|---|
| talent_id | system | no (alias instead) |
| name, email, phone, location, rate, hours, availability | form | **no**, local only |
| primary_profession | form dropdown (professions.yaml) | yes |
| secondary_professions (up to 2, GUESS) | form dropdown | yes |
| fields / industries | form dropdown (industries.yaml) | yes |
| seniority | form dropdown (seniority levels in config) | yes |
| years_experience | form | yes |
| target_titles | form | yes |
| core_skills (up to 15, GUESS) | LLM from resume, person confirms | yes |
| summary_short (at most 80 words) | LLM from resume, scrubbed, person confirms | yes |
| languages (EN/FR) | form | yes |

### Principles

- **Profession, field and seniority come from form dropdowns, not the LLM.** These decide
  the prefilter, so they must be reliable, and dropdowns cost nothing.
- **Use the LLM only for what a form can't capture:** skills and a short matching summary
  from the resume. It costs one call per contractor, once (about 2–4k tokens, GUESS), and a
  new call only when the resume changes.
- **Scrub resumes locally before any call.** Remove the contact header, name, emails, phones,
  URLs and addresses. Resumes are the most personal data in the project and the free tier's
  terms apply.
- **People confirm.** The LLM output goes to a staging file
  (`data/contractors/intake_staged.csv`) for a person to check. Approved rows go into the
  master workbook, and `build-contractors` builds `contractors.csv` as today. Nothing writes
  to the human-maintained files directly (CLAUDE.md).
- **Never invent.** Skills must appear in the resume or form. The intake prompt asks for
  evidence quotes, and they are checked against the resume text the way match quotes are.

### Out of scope here

How contractors upload: hosting a form or file drop is an external service and needs its
own decision (Q6).

## 10. Phases

Every phase: tests with English and French examples, network mocked, LF endings, a
LOG-UPDATE entry per commit.

| Phase | Work | API calls | Depends on |
|---|---|---|---|
| **0. Contracts** | `ContractorProfileV1` schema; `titles:` patterns and dropdown lists in `professions.yaml`; `llm:` section in settings; `GEMINI_API_KEY=` in `.env.example`; workbook column spec for profession and field | 0 | — |
| **1. Foundation** | `llm/provider.py` (interface), `llm/gemini.py` (httpx), `llm/usage.py` (log + budget guard), `llm/anonymize.py`, `llm/payload.py` (pruned job), `llm/prompt.py` + output schemas, evidence-quote check, `llm-preview` command | 0 | 0 |
| **2. Local measurement** | `llm-preview --stats`: tokens per job, per contractor, per call for setups B and C; count of active jobs; projected requests and days at our real RPD | 0 | 1 |
| **3. Prefilter** | Backfill profession and field for the 38 contractors (a person, in the workbook); `llm/classify.py` (rules); offline recall check on blind labels | 0 | 0, backfill |
| **4. First call** | One real call on one job; check the response shape, token counts and log line | 1 | 1 |
| **5. Pilot** | Run on the jobs in the blind-label set (about 60–80 calls, GUESS), with and without the prefilter, Flash vs Flash-Lite. Score against the 331 blind labels with `scripts/eval/`. Stored v0 numbers are a comparison only; v0 is not run | 60–240 over several days | 4, 3 |
| **6. Wire in** | `match` and `run-once` call the judge; v0 commands taken out of the v1 path; `LlmMatch` repository and `llm_matches.csv`; exports read it; batched classifier call for unclear jobs | daily volume | 5 |
| **7. Docs** | README, PROJECT_CONTEXT (new decisions), DEVELOPMENT, quick_start, LOG-UPDATE; CLAUDE.md stack line ("Ollama first, Gemini disabled") is yours to edit | 0 | 6 |
| **B. Intake** (parallel) | Resume scrubber, intake prompt and schema, staging file, review step | 1 per contractor, once | 0 |

### Output schema (phase 1)

- **One entry per contractor in the call:**
  - alias;
  - verdict (`fit` / `partial` / `no`);
  - a one-line reason.
- **For the top `llm.top_k` (5) only:**
  - a 0–100 fit score (GUESS scale, used for ordering only);
  - requirements met, requirements missing;
  - red flags (seniority gap, different profession, other);
  - evidence quotes from the job and from the contractor text.
- **For the whole job:** a `no_fit` flag when nobody fits.

Every quote is checked locally against the text sent. A quote that isn't found is removed,
and the match is flagged `unverified_evidence`. The output never triggers any action;
it is data for the reviewer.

## 11. Open questions

| # | Question | Default if unanswered |
|---|---|---|
| Q1 | Our real free-tier limits (RPM, TPM, RPD, TPD) per model, from AI Studio | Phase 2 projections use 10 RPM / 250k TPM / 250 RPD and say so |
| Q2 | Employer names in the short summary: keep or strip? | Keep (they help judge industry); revisit if a profile becomes identifiable |
| Q3 | Prefilter recall target before turning it on | At most 2% of labelled fits lost |
| Q4 | Send job pay and contractor rate band so Gemini can flag rate gaps? | No: rate stays local; a local rate note can be added after matching without v0 code |
| Q5 | Fit score scale: 0–100 numbers or verdict only? | Both: verdict for decisions, number only to order the top-k |
| Q6 | How contractors upload resumes and forms (which service hosts it) | Out of scope until decided; phase B starts from files dropped in a local folder |
| Q7 | Seniority list for dropdowns: reuse `normalization.yaml` levels? | Yes |

## 12. Risks

- **Free-tier limits change without notice.** Limits live in config, the budget guard reads
  the usage log, and a daily-limit 429 stops the run cleanly.
- **Data terms on the unpaid tier.** We minimize and anonymize, but trimmed profiles are
  still about real people. This needs a privacy decision (PIPEDA) by the team, not just code.
- **Prefilter drops fits.** It stays off until the offline recall check passes, and
  `unclear` jobs always get the full roster.
- **Prompt injection from postings.** The posting is wrapped in delimiters and marked as
  untrusted in the prompt, and the output is validated JSON that triggers nothing.
- **Model drift.** Results change when Google updates a model. The model name and prompt
  version are stored on every match, and a change re-judges jobs (the skip key changes).
- **Roster growth.** Without the prefilter, a 200-contractor roster is about 50k tokens a
  call. The prefilter becomes required, not optional, past about 100 contractors (GUESS).
