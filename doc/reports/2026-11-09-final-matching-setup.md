# Final matching setup: decision and results

**Date:** 2026-11-09 · **Prompt:** `2026-11-09.v15` (screening `2026-11-08.v14`) · **Data:** 23 development jobs, 38 contractors
**Reference run:** `run-20261109T073821Z-36f260` · **Full test history:** [2026-11-08-matching-run-v14.md](2026-11-08-matching-run-v14.md)

## The setup

| Stage | Model | Thinking | Per call |
|---|---|---|---|
| **Screening:** every contractor, one-line verdicts, shortlist of up to 5 who fit or partly fit | `gemini-3.5-flash-lite` | `medium` | 1 job |
| **Deep review:** justification, Meets table, missing items, flags, score, final verdict | `gemini-3.6-flash` | `medium` | **2 jobs** (1 when a job is alone) |
| Fallback reviewer 1 | `gemini-3-flash-preview` | `medium` | 2 jobs |
| Fallback reviewer 2 | `gemini-3.5-flash-lite` | `medium` | 2 jobs |

- **The fallback rule:** a reviewer that answers "daily limit reached" or doesn't answer within 120 s is skipped for the rest of the run, and the next one reviews. The next run tries the first again.
- **Nobody shortlisted:** a job with nobody shortlisted needs no review call and shows "no match".
- **Shown to the recruiter:** up to 3 fits, otherwise the single best partial, otherwise nothing.
- **The score decides the verdict:** 70+ fit, 40–69 partial, below 40 not shown.
- **Settings** (`config/settings.yaml`): `llm.model`, `llm.review_models`, `llm.review_batch_size: 2`, `llm.thinking_level: medium`, `llm.model_thinking: {}`.

## Results of the reference run

| Measure | Value |
|---|---|
| Review calls | 10, all answered by `gemini-3.6-flash` (no fallback needed) |
| Tokens | 113,149 for the reviews (the 23 screenings were reused) |
| Outcome | **5** jobs with clear fits, **14** with a best partial, **4** with no match |
| Same contractor verdict as the benchmark (3.6 Flash, 1 job per call) | **80%**; 21 of 23 job outcomes the same; scores +3.7 on average |
| Same verdict as an earlier run of this exact setup | **86%**; scores +0.4 (the setup's own run-to-run variation) |
| "Meets" rows failing the word-for-word check | **0** |
| Codenames, IDs, technical words or personal traits in shown text | 0 |

**Known hard cases, handled correctly:**
- **IT project manager job:** the marketing-programs project manager is **partial 62**, with the PMP and IT-scale gaps flagged.
- **CIBC video creative director job:** **partial 58**, with the missing video production experience flagged.
- **Bilingual partnership job:** **partial 50**, with French listed as missing.
- **ECH marketing manager job:** **partial 62**, a candidate the screening had missed in v12.

**Remaining lenient case:** social media community manager, with 2 fits (70, 75) although the required listening tools are missing.

## Time and cost

| | Per job | 10 new jobs | 23 jobs |
|---|---|---|---|
| Screening (Flash Lite) | about 13–16 s, about 13k tokens | about 2.5 min | about 6 min |
| Review (3.6 Flash, 2 per call) | about 22 s, about 6k tokens | about 3.5 min | about 7 min |
| **Full run** | **about 33 s, about 19k tokens** | **about 5–6 min** | **about 13 min** |

- **Daily capacity (free tier, per Google project):**
  - **About 40 jobs reviewed by 3.6 Flash** (about 20 review requests, observed), then 3 Flash and Flash Lite take over.
  - Flash Lite allows 500 requests a day for screening.
- Already-judged jobs are cached and cost nothing to re-run.
- Nearly all the time is spent waiting on Google, so the client's 2019 MacBook should see about the same.

## Why this setup (summary of the tests)

All numbers are verdict agreement with the 3.6 Flash one-job benchmark, on the same 23 jobs.

| Setup tested | Agreement | Verdict |
|---|---|---|
| **3.6 Flash review, 2 jobs per call, Flash Lite `medium` screening** | **86% / 80%** (two runs) | **chosen** |
| Flash Lite for everything, thinking `high`, 2 per call | 78% | more lenient; repeats the IT project manager mistake |
| 3.6 Flash, 3 jobs per call | 76% | lenient; 3 creative directors rated fit without video |
| Flash Lite `high`, 1 per call | 75% | batching isn't Flash Lite's issue; its judgement is |
| Flash Lite `high` screening + 3.6 / 3 Flash review | 73% | more borderline shortlists; 3 Flash fallback lenient |
| Flash Lite review, `medium` | 71% | |
| 3.6 Flash, 3 per call, thinking `high` | 70% | more thinking made batches more lenient, not more accurate |
| NVIDIA `nemotron-3-ultra` | 68% | 2 minutes per review; rejects on rate or location; removed |
| Groq `gpt-oss-120b` | 63% | 8,000 tokens per minute too tight; removed |

**What we learned:**
- **A stronger reviewer matters most.** Flash Lite alone repeatedly rated an adjacent profession as a fit.
- **2 jobs per call** halves the strong model's requests with little accuracy cost. **3 per call** makes it more lenient, because the model scores candidates relative to the other jobs in the call.
- **More thinking helps up to `medium`, then stops.** At `minimal` the rules were ignored. Beyond `medium`, extra reasoning mostly argued the model into more generous judgements; the facts stayed correct (0 invented "Meets" rows).
- **Run-to-run variation is real:** about 14% of verdicts differ between two runs of the same setup. Borderline scores (about 45–75) are a prompt for the recruiter to look, not a final answer.

## Open items

1. **Enforce "a missing core requirement → at most partial" in the app.** The reviewer would label each gap core or secondary, and the app would cap the score. That's the most effective remaining fix for leniency; it holds across models, batches and runs.
2. **Benchmark `gemini-3-flash-preview`** before relying on it as the second reviewer (in one run it repeated the creative-director mistake).
3. **Copy the real free-tier limits** for `gemini-3.6-flash` and `gemini-3-flash-preview` from AI Studio into `llm.model_limits` (currently guesses of 20 a day).
4. **Fill `years_experience`** in the contractor list (empty for every contractor), so "N+ years" requirements can be judged.
5. **Clean one admin note** from a contractor's proof points in the workbook.
