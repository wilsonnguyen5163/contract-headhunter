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

## Comparison: Flash Lite as the reviewer

The v14 results above are the **judgement benchmark**, saved to `data/eval/benchmark-v14-3.6flash.json`.

The same 23 jobs were then reviewed again by `gemini-3.5-flash-lite` (thinking `medium`), reusing the benchmark's screening answers, so only the reviewer changed. The experiment wrote to `data/llm_results/experiments/review-flash-lite-v14/`; shortlists weren't touched.

| | 3.6 Flash (benchmark) | Flash Lite |
|---|---|---|
| Review calls | 19 | 19, all usable |
| Avg tokens per review | 7,451 | **4,262** (−43%) |
| Review time | not timed separately | 219 s for 19 reviews (about 11.5 s each) |
| Jobs: fit / partial / no match | 4 / 14 / 5 | 6 / 12 / 5 |
| Same job outcome | | 19 of 23 |
| Same contractor verdict | | 50 of 70 (71%) |
| Score difference (Lite minus 3.6) | | average +0.6, but from −17 to +25 |
| Meets rows / missing items per shown contractor | 2.5 / 3.1 | 2.7 / 2.6 |
| Rows dropped by the word-for-word check | 0 | 0 |

**Verdict changes, 3.6 → Lite:** 9 no → partial, 2 partial → fit, 6 partial → no, 3 fit → partial. The rest are unchanged.

**The 4 jobs whose outcome changed:**
- **Bilingual partnership:** Lite shows a partial (55) for a brand executive with no French. French is in the job title and so core; 3.6's no match is right.
- **Social media community manager:** Lite shows a fit (85) for a strategist missing the required social listening and dashboard tools. 3.6's partial is right.
- **Machine learning engineer:** Lite rates the fractional CTO a fit (75) despite the required Master's or PhD not being in the profile. 3.6's partial is right.
- **Business analyst:** 3.6 shows a partial (50), Lite shows nothing. Both are defensible.

**Conclusion:**
- Flash Lite is about 40% cheaper per review and follows the format equally well.
- But in about 1 in 4 cases it judges differently, mostly by being lenient on core requirements: the same weakness that led to the stronger reviewer.
- Keep `gemini-3.6-flash` first in the chain. Flash Lite is fine as the last fallback, but its reviews are less reliable.

## Comparison: Groq `openai/gpt-oss-120b` as the reviewer

This is the same setup as the Flash Lite comparison: the same benchmark screenings, with only the reviewer changed. It's the strongest general reasoning model on Groq's list for our key, run with reasoning effort `medium` and the same v14 prompts (the answer layout sent as a JSON schema).
- **Groq free limits for our key** (read from its response headers): 1,000 requests a day, **8,000 tokens per minute**.
- **Groq usage:** 20 calls (including a 90-token probe), 103,322 tokens.

| | 3.6 Flash (benchmark) | Groq gpt-oss-120b | Flash Lite |
|---|---|---|---|
| Reviews answered | 19 of 19 | **15 of 19** (3 cut off at the 5,000-token completion cap, 1 refused as larger than 8,000 tokens per minute) | 19 of 19 |
| Avg tokens per review | 6,863 | 5,269 | 3,683 |
| Time per review | about 19 s | **about 64 s** (throttled by the 8,000-per-minute limit) | about 11.5 s |
| Same job outcome (19 comparable jobs) | | 15 of 19 | 16 of 19 |
| Same contractor verdict | | **32 of 51 (63%)** | 37 of 51 (73%) |
| Score difference vs 3.6 | | average **+5.2**, from −27 to +37 | −1.1, from −17 to +25 |
| "Meets" rows dropped by the word-for-word check | 0 | **25** | 0 |

**Verdict changes vs 3.6 Flash:** 11 no → partial, 6 fit → partial, 1 partial → fit, 1 partial → no.

What the changes show:
- **More lenient on a different profession.** For the bilingual partnership job it rated 5 contractors from other professions (operations, content writing, copywriting, marketing), all lacking French, as **partial (45–62)**; 3.6 Flash rated them "no". It also rated a fractional CFO partial 68 for the compensation specialist job (3.6 Flash: no 38).
- **Weaker grounding.** 25 of its "Meets" rows quoted words that aren't in the profile or job (Gemini: 0). The check removed them, so nothing invented reached the screen, but it shows the model is less careful about copying.
- **Not reliable on the free tier.** About 1 in 5 reviews failed because 8,000 tokens a minute is too small for a review that includes the reasoning.

**Conclusion:**
- Ranking as a reviewer for this task: **3.6 Flash > Flash Lite > Groq gpt-oss-120b**.
- Groq is not added to the review chain.
- Groq's `llama-prompt-guard-2` models could still serve a different purpose: scanning job postings for prompt-injection attempts.

## Comparison: NVIDIA NIM `nvidia/nemotron-3-ultra-550b-a55b` as the reviewer

This is the same setup as the comparisons above: the same benchmark screenings, with only the reviewer changed. It's NVIDIA's largest general model that answers on our free account; `nemotron-3-super` and `kimi-k2.6` returned "not found for account".
- It uses the model's default reasoning and the same v14 prompts.
- The answer timeout was raised to 10 minutes for this test; the first try timed out at 120 s.
- **NVIDIA usage:** 24 calls (4 probes, 1 timeout, 19 reviews), about 145k tokens. NVIDIA sends no rate-limit headers.

| | 3.6 Flash (benchmark) | NVIDIA nemotron-3-ultra | Flash Lite | Groq gpt-oss-120b |
|---|---|---|---|---|
| Reviews answered | 19 of 19 | 18 of 19 (1 cut off after 15k tokens of thinking) | 19 of 19 | 15 of 19 |
| Time per review | about 19 s | **about 114 s** | about 11.5 s | about 64 s |
| Avg tokens per review | about 7,300 | about 7,000 (thinking up to 15k) | about 4,300 | about 5,300 |
| Same contractor verdict | | 44 of 65 (68%) | 46 of 65 (71%) | 32 of 51 (63%) |
| Score difference vs 3.6 | | average **−8.1**, from −50 to +25 | +0.3 | +5.2 |
| "Meets" rows dropped by the word-for-word check | 0 | **17** | 0 | 25 |

**Verdict changes vs 3.6 Flash:** 10 partial → no, 5 fit → partial, 4 no → partial, 1 partial → fit, 1 fit → no.

What the changes show:
- **The strictest reviewer, and against our rules.** It rated the copywriters and content strategists for the UX content strategist job "no" (15–25). v13 says the same profession missing a core specialty is partial.
- **It lets rate and location decide the verdict.** For the event coordinator job it rated the project coordinator "no" (25) mainly because of an $80/hour rate against the budget and a Vancouver location for a Victoria job. 3.6 Flash rated the same person fit 75, with those points as flags.
- **Lenient on the social media job** like Flash Lite: fit 85, while missing the required listening tools.
- **Weaker grounding:** 17 "Meets" rows weren't in the texts (removed by the check).
- **Too slow** for the app: about 2 minutes per review on the free endpoint.

**Conclusion:**
- Ranking as a reviewer for this task: **3.6 Flash > Flash Lite > NVIDIA nemotron-3-ultra > Groq gpt-oss-120b**.
- NVIDIA is not added to the review chain.

## Comparison: 3.6 Flash reviewing 2 jobs per call (v15, now the default)

This is the same setup again: the same benchmark screenings, the same model (`gemini-3.6-flash`) and the same review rules, but **2 jobs per review call** instead of 1 (`llm.review_batch_size: 2`). Each job is judged only against its own shortlist and posting.

| | 3.6 Flash, 1 job per call (benchmark) | **3.6 Flash, 2 jobs per call** | Flash Lite, 1 per call |
|---|---|---|---|
| Review calls for 19 jobs | 19 | **10** (all answered, no job left out) | 19 |
| Tokens | about 139k | **111,987** (about 5.9k per job) | about 81k |
| Time | about 19 s per job | 358 s in all: 36 s per call, **about 19 s per job** | about 11.5 s per job |
| Same job outcome | | **20 of 23** | 19 of 23 |
| Same contractor verdict | | **60 of 70 (86%)** | 50 of 70 (71%) |
| Score difference vs benchmark | | average +3.3, from −15 to +25 | +0.6, from −17 to +25 |
| "Meets" rows dropped by the check | 0 | 0 | 0 |

**Verdict changes:** 6 no → partial (mostly scores of 30–35 becoming 42–60, around the 40 cut-off), 2 partial → fit, 1 fit → partial, 1 partial → no.

**The 3 jobs whose outcome changed:**
- **Social media community manager:** 2 fits (75, 70) instead of partial 65, although the required listening tools are still missing. The single review was stricter, and right.
- **Bilingual partnership:** partial 45 for the digital marketer who has French but no partnership or social-services background. The single review said no (35); this is a borderline call.
- **Machine learning engineer:** the fractional CTO dropped from partial 50 to "no" (35), so the job now shows no match. Either call is defensible.

**Conclusion:**
- Reviewing 2 jobs per call keeps **86% verdict agreement** with single reviews (Flash Lite: 71%). It **halves the review requests** (3.6 Flash's daily limit counts requests), uses about 20% fewer tokens, and takes the same time per job.
- It's slightly more generous near the 40-point cut-off.
- Part of the 14% difference may be ordinary run-to-run variation; we haven't measured it (that would need re-running the single reviews).
- **Batches of 2 are the default.**

### And with 3 jobs per call

The same test with `review_batch_size: 3`: 7 calls, all answered (no job left out, no cut-off answers), **103,306 tokens**, 333 s. The largest call used about 23k of the 24,576-token output allowance.

| | 1 per call (benchmark) | 2 per call | **3 per call** |
|---|---|---|---|
| Review calls for 19 jobs | 19 | 10 | **7** |
| Tokens | about 139k | 111,987 | **103,306** |
| Same contractor verdict as the benchmark | | 86% | **76%** |
| Average score vs the benchmark | | +3.3 | **+5.7** |
| Missing items per shown contractor | 3.1 | 2.9 | **2.3** |
| "Meets" rows dropped by the check | 0 | 0 | 0 |

**With 3 per call the reviews get more lenient and less thorough:**
- 11 no → partial.
- Fewer missing requirements listed.
- **Creative director, media centre:** 3 creative directors shown as **fit 75** without the core video production experience (1 or 2 per call: partial 55–60).
- **Graphic designer:** scores inflated to 92–95.

Agreement drops to 76%, close to Flash Lite's 71%.

**Conclusion:** stay at **2 per call**. Going from 2 to 3 saves only about 8% of tokens and 3 requests for 19 jobs, and clearly costs accuracy.

### And with 3 per call, review thinking `high`

To check whether more reasoning fixes the batch-of-3 drop, this ran with `llm.review_thinking_level: high` (a new setting; screening stays `medium`), `max_output_tokens` 49,152 and a 300 s timeout, for this test only.
- 7 calls, all answered, **121,725 tokens**, 487 s.
- Thinking rose to about 4.1k per job (`medium`: about 2.8k).

| | 1 per call | 2 per call | 3 per call | **3 per call, thinking high** |
|---|---|---|---|---|
| Same verdict as the benchmark | | 86% | 76% | **70%** |
| Average score vs the benchmark | | +3.3 | +5.7 | **+6.6** |
| Missing items per shown contractor | 3.1 | 2.9 | 2.3 | **2.0** |
| Tokens | about 139k | 112k | 103k | **122k** |
| Time | about 19 s per job | about 19 s per job | about 18 s per job | **about 26 s per job** |

**More thinking made it worse, not better:**
- More generous again: 13 no → partial and **7 partial → fit**.
- The CIBC video creative director job still shows 3 creative directors as fit (78, 75, 72) without video production.
- Fewer missing items listed.
- The drop with 3 postings per call isn't from too little reasoning: the model rates more generously when it compares more jobs at once.

**Conclusion:** batches of 2 at thinking `medium` stay the default. `llm.review_thinking_level` stays in the code, unset.

## Comparison: Flash Lite for everything, thinking `high`

`gemini-3.5-flash-lite` did **both the screening and the review**, at thinking `high` (the highest Gemini 3 level). Reviews were 2 jobs per call; the screenings were new. For this test the output cap was 65,536 and the timeout 300 s.
- 34 calls, all answered (23 screenings, 11 reviews), no cut-offs, **483,543 tokens**, 812 s (about 35 s per job).
- Screening: about 15–16k tokens each (thinking about 5k). Reviews: about 14k per call (thinking 7–9k).

| | 3.6 Flash review, 1 per call (benchmark) | 3.6 Flash, 2 per call (default) | Flash Lite review, `medium` | **All Flash Lite, `high`** |
|---|---|---|---|---|
| Same job outcome | | 20 of 23 | 19 of 23 | **19 of 23** |
| Same contractor verdict | | 86% | 71% | **78%** (60 contractors reviewed by both) |
| Score difference vs benchmark | | +3.3 | +0.6 | **+6.5**, from −18 to +35 |
| Missing items per shown contractor | 3.1 | 2.9 | 2.6 | 2.5 |
| "Meets" rows dropped by the check | 0 | 0 | 0 | 0 |

**Screening at `high` didn't miss anyone:**
- It sent 78 contractors to review, against the benchmark's 70; 60 were in both.
- Every contractor the benchmark ended up showing was also shortlisted.

**Review at `high` is better than Flash Lite at `medium` (78% against 71%), but more generous:**
- 8 no → partial and 4 partial → fit.
- **Project manager:** the marketing-programs project manager is back to **fit 72** for the IT infrastructure project manager job. That's the original mistake the stronger reviewer fixed.
- **Social media manager:** 2 fits without the required listening tools.
- **Facilitators:** a partial (60) where the benchmark found no match.

**Conclusion:**
- All Flash Lite at `high` holds up reasonably well: no missed candidates, solid grounding, and it runs entirely on Flash Lite's large quota (500 requests a day).
- But it judges core gaps more leniently and repeats a known mistake.
- It's a sensible **fallback**, not the default. Giving Flash Lite thinking `high` when it acts as the last fallback reviewer would raise the fallback's agreement from about 71% to about 78%.
- **Done:** Flash Lite now uses thinking `high` by default (`llm.model_thinking`).

### And with 1 job per review call

The same Flash Lite `high` screenings, reviewed by Flash Lite `high` **one job per call**, to compare with the 1-job benchmark directly.
- 21 calls, all answered, **162,862 tokens** (about 7.8k per review), 393 s (about 19 s per review).

| | 3.6 Flash, 1 per call (benchmark) | **Flash Lite `high`, 1 per call** | Flash Lite `high`, 2 per call | Flash Lite `medium`, 1 per call |
|---|---|---|---|---|
| Same job outcome | | **17 of 23** | 19 of 23 | 19 of 23 |
| Same contractor verdict | | **75%** | 78% | 71% |
| Score difference vs benchmark | | **+6.1** | +6.5 | +0.6 |
| Missing items per shown contractor | 3.1 | 2.8 | 2.5 | 2.6 |
| "Meets" rows dropped by the check | 0 | 0 | 0 | 0 |

**One job per call didn't make Flash Lite any closer to the benchmark.** The same lenient pattern showed up:
- the marketing-programs project manager was **fit 72** again for the IT job;
- **2 fits** for the marketing and communications manager without the "essential" Adobe tools;
- 2 fits for the social media manager without the listening tools;
- a **fit 85** for the facilitators job, which had no match in the benchmark (that run's 2-per-call review had rated the same job partial 60).

Flash Lite's two `high` runs (1 and 2 per call) agree with each other on only 78% of verdicts, about the same as with the benchmark. Its differences come from **run-to-run variation and leniency on core requirements, not from batching**.

**Conclusion:**
- For Flash Lite, 2 jobs per call costs nothing in accuracy and halves its requests, so the batch size stays at 2 for every model.
- The accuracy gap is the model itself: 3.6 Flash stays the primary reviewer.

## Final run on the chosen setup (2026-11-09)

| Stage | Model | Thinking | Jobs per call |
|---|---|---|---|
| Screening | `gemini-3.5-flash-lite` | `high` | 1 |
| Review | `gemini-3.6-flash` → `gemini-3-flash-preview` → `gemini-3.5-flash-lite` | `medium`, `medium`, `high` | 2 (1 if alone) |

- **Run** `run-20261109T070959Z-8ee3be`: 35 calls (23 screenings, 12 reviews), all 23 jobs usable, **490,278 tokens**, 958 s (about 42 s per job).
- **3.6 Flash answered "daily limit reached" after 8 review calls (16 jobs).** The remaining 3 batches (5 jobs) went to `gemini-3-flash-preview` automatically.
- **Result:** 8 jobs with clear fits, 12 with a partial, 3 with no match.
- **Against the benchmark:** 18 of 23 outcomes the same, **73%** of contractor verdicts the same, scores +7.5 on average. 1 "Meets" row failed the check.

**Why this is below the 86% of the batch-of-2 test:** three things differ from that test.
1. **The screening is now Flash Lite at `high`.** It shortlists more people (78 against 70 in its test), so reviews include more borderline candidates.
2. **5 jobs were reviewed by the fallback `gemini-3-flash-preview`, which was never benchmarked.**
   - It rated 3 creative directors **fit (75–78)** for the CIBC video job: the same mistake as 3.6 Flash in batches of 3.
   - It gave a partial for the bilingual job.
3. **Run-to-run variation of 3.6 Flash itself.** This time it rated the marketing-programs project manager **fit 75** for the IT job (55–65 in earlier runs) and a facilitator fit 85.

**Open points for the next tuning step:**
- Benchmark `gemini-3-flash-preview` before relying on it as the second reviewer.
- Consider the "core requirement missing → at most partial" rule enforced by the app, which would hold whatever the model or run.
- Fill `years_experience` in the contractor list.

## Setup reverted to the best-measured one (2026-11-09, run `run-20261109T073821Z-36f260`)

The user went back to **Flash Lite `medium` screening + 3.6 Flash reviews, 2 jobs per call (1 if alone)**; fallbacks 3 Flash, then Flash Lite, all at `medium`.
- New API key. The 23 saved `medium` screenings were reused, so only the reviews were sent: **10 calls, all on 3.6 Flash** (no fallback), **113,149 tokens**, 410 s.

| | Result |
|---|---|
| Outcome | 5 jobs with clear fits, 14 with a partial, 4 with no match |
| Against the benchmark | 21 of 23 outcomes the same, **80%** of verdicts, +3.7 points |
| Against the earlier test of the same setup (the 86% run) | 22 of 23 outcomes, **86%** of verdicts, +0.4 points: the setup's own run-to-run variation |
| "Meets" rows dropped by the check | 0 |

The project manager job is back to **partial 62** for the marketing-programs project manager, and the CIBC creative director job to **partial 58**. The social media manager still gets 2 fits (70, 75), as in the earlier batch-of-2 test.

**Timing:**
- 3.6 Flash took about 41 s per review call (2 jobs), about 22 s per job.
- Flash Lite screening takes about 13–16 s per job.
- A full run from scratch is about **33 s per job**: about 13 minutes for these 23 jobs, about 5–6 minutes for 10 new jobs.

Sources: the run log, `data/llm_usage.csv`, `data/llm_matches.csv` and the saved answers in `data/llm_results/judgments/` (prompt `2026-11-08.v14`). Change history: [LOG-UPDATE.md](../LOG-UPDATE.md).
