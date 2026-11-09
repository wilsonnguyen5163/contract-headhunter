# Matching run report: prompt v14, development data

**Date:** 2026-11-08 · **Run IDs:** `run-20261109T022342Z-163ed9` (main), `run-20261109T023611Z-8a92e4` (one review redone)
**Data:** 23 development jobs (`data/jobs.csv`, trimmed from 82: 10 fit, 10 partial, 3 no-match under the earlier prompt), 38 contractors (all sent).

## Setup

| | |
|---|---|
| Prompt | `2026-11-08.v14` (`src/jobmatch/llm/prompt.py`) |
| Flow | 2 calls per job: screening of all 38 contractors, then a deep review of the shortlist |
| Screening model | `gemini-3.5-flash-lite`, thinking `medium` |
| Review chain | `gemini-3.6-flash` → `gemini-3-flash-preview` → `gemini-3.5-flash-lite` (next model on "daily limit reached" or a timeout) |
| Shortlist | up to 5, only fit or partial (fewer if fewer fit; none means no review) |
| Shown to the reviewer | up to 3 fits, else the best partial, else nothing |
| Verdict rule | the score decides: 70+ fit, 40–69 partial, below 40 no |
| Other settings | temperature 1, seed 418093, `max_output_tokens` 24,576 (raised during this run, see Issues) |

## Timing

| Measure | Value |
|---|---|
| Main run | 730 s for 23 jobs (one review cut off and redone separately in 35 s) |
| **Average per job** | **about 32 s** (2 calls, or 1 when nobody is shortlisted) |
| Average per call | about 17 s (43 calls) |
| Pacing waits | none needed: the calls themselves are the bottleneck, not the per-minute limits |

The time is spent waiting on Google (mostly thinking), so the client's 2019 MacBook should see about the same.

**Compared with earlier setups (same 23 jobs):**

| Setup | Calls per job | Per job | Full run |
|---|---|---|---|
| v11: 1 Flash Lite call, no thinking | 1 | about 10 s | about 230 s |
| v12: Flash Lite screening + 3.6 Flash review, thinking `medium` | 1–2 | about 30 s | 682 s |
| **v14** (this run) | 1–2 | **about 32 s** | 730 s |

## Token usage

| Call | Count | Avg input | Avg output | Avg thinking | Avg total | Range |
|---|---|---|---|---|---|---|
| Screening (`gemini-3.5-flash-lite`) | 23 | 8,719 | 1,586 | 2,639 | **12,945** | 11,287–14,357 |
| Review (`gemini-3.6-flash`) | 18 | 2,371 | 1,125 | 3,804 | **7,300** | 3,614–11,103 |
| Review (`gemini-3-flash-preview`) | 1 | 1,928 | 918 | 3,516 | 6,362 | |
| Review cut off (`gemini-3-flash-preview`) | 1 | 2,394 | 1,204 | 11,069 | 14,667 (no answer) | |

- **Whole run:** 44 calls, **453,964 tokens** (442,861 main + 11,103 redo).
- **Per job:** about 19.7k tokens on average (one screening, plus a review for 19 of the 23 jobs).
- **Thinking is the largest part of a review:** about half its tokens.
- **Quota events:** `gemini-3.6-flash` answered "daily limit reached" once. That job went to `gemini-3-flash-preview` as designed, then was cut off (see Issues).
- **Local usage log for the day** (both API keys together): 2.73M tokens. Google counts each key's project separately.
- **Capacity (observed, not confirmed):**
  - Flash Lite allows 500 requests a day, about 500 screenings.
  - `gemini-3.6-flash` started refusing after about 20 reviews on a key. Its real free limit isn't published; copy it from AI Studio into `config/settings.yaml` (`llm.model_limits`).

## Results

| Outcome | Jobs |
|---|---|
| Clear fit (up to 3 shown) | 4 |
| No clear fit: best partial shown | 14 |
| No match | 5 |

| Job | Shown (verdict, score) |
|---|---|
| Bilingual (FR/EN) partnership specialist | no match |
| Business analyst | partial 50 |
| Client services partner | partial 45 |
| Content copywriter | fit 92, fit 85, fit 75 |
| Creative director, media centre | partial 55 |
| Data integration architect | no match |
| Demand generation partner | fit 75 |
| Digital marketing & email designer | partial 60 |
| ECH manager, marketing | partial 55 |
| Event marketing coordinator | fit 75, fit 70 |
| Facilitators, performers | no match |
| Finance manager | partial 58 |
| Graphic designer | fit 88, fit 80, fit 78 |
| Human resources administrator | partial 50 |
| Machine learning engineer | partial 50 |
| Project manager | partial 65 |
| Senior business analyst (treasury systems) | no match |
| Senior compensation specialist | partial 58 |
| Senior financial analyst, financial reporting | partial 45 |
| Senior full-stack developer (Java/Angular) | no match |
| Marketing and communications manager | partial 60 |
| Social media community manager | partial 65 |
| UX content strategist | partial 52 |

**Development across the session (same 23 jobs):**

| Prompt | Clear fit | Partial | No match | Notes |
|---|---|---|---|---|
| v2 (before the trim) | 10 | 10 | 3 | single Flash Lite call |
| v12 | 3 | 10 | 10 | strong reviewer, too strict ("no" for the same profession missing a specialty) |
| **v14** | **4** | **14** | **5** | "no" only for a different profession; the 5 no-match jobs have nobody in that line of work |

## Quality metrics

| Metric | Value |
|---|---|
| Screening verdicts (23 jobs × 38 contractors) | 20 fit · 109 partial · 743 no |
| Shortlist size per job | 0 (4 jobs), 1 (4), 2 (2), 3 (1), 4 (1), 5 (11); average 3.0 |
| Final verdicts of the 70 deep reviews | 11 fit · 40 partial · 19 no |
| Reviewer changed the screening | 27 times: 18 partial → no, 8 fit → partial, 1 fit → no (never upgraded) |
| "Meets" rows checked word for word | 134 kept, **0 dropped** |
| "Meets" rows per shown contractor | 1–5, average 2.5 |
| Requirement summaries | 6.2 words on average (asked: 8 or fewer) |
| Reasons | 28 words on average (asked: 2 sentences, at most 45) |
| Scores shown | 45–92, average 64.6 |
| Codenames, talent IDs or technical words in shown text | 0 |
| Age or other personal traits mentioned in shown text | 0 |
| Eligibility flags (age program, citizenship, clearance) | 5, only on the job that states such a condition |
| Answer structure problems | 1 (screening missed the verdict for 2 of 38 contractors on one job) |

## Issues during the run

1. **Review cut off.** `gemini-3-flash-preview` thought for 11,069 tokens and hit the 12,288 output limit (thinking counts toward it). The job's screening had been saved, so only the review was redone after raising `max_output_tokens` to 24,576. On the redo, `gemini-3.6-flash` answered (11,103 tokens).
2. **3.6 Flash quota.** On the first key it refused after about 20 reviews; on the second key after about 25. The chain handled it, but most reviews per day will depend on that model's real limit.
3. **`gemini-3.8-flash`** (tried earlier the same day) answered "high demand" or timed out on every review, and was removed from the chain.

## Open items

- Copy the real free-tier limits for `gemini-3.6-flash` and `gemini-3-flash-preview` from AI Studio into `config/settings.yaml`.
- `years_experience` is empty for every contractor in `contractors.csv`. Postings that ask for "N+ years" can only be judged on seniority labels; filling it would sharpen verdicts.
- One contractor's proof points contain an admin note (a contract countersigned date); clean it in the workbook.
- Possible speed-up (untested): thinking `low` for screening only, keeping `medium` for reviews.

Sources: the run log, `data/llm_usage.csv`, `data/llm_matches.csv` and the saved answers in `data/llm_results/judgments/` (prompt `2026-11-08.v14`). Change history: [LOG-UPDATE.md](../LOG-UPDATE.md).
