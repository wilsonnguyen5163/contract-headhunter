# Prototypes: what was tried and what was decided (v0)

The v0 matcher was tested against alternatives from 2026-11-02 to 2026-11-04. This page is the summary. Every table and number is in [archive/PROTOTYPES-v0-full.md](archive/PROTOTYPES-v0-full.md).

## How it was measured

**Labels:** 331 job–contractor pairs, labelled **blind** (no scores shown) over three sheets ([LABELLING.md](LABELLING.md)). Consensus: 100 fit, 183 no, 48 partial.
- A person labelled 13 pairs, and agreed with both AI judges on all 13.
- ChatGPT and Claude labelled the rest.
- Earlier, non-blind labels by Claude Code (`data/eval/llm_judge_labels.csv`) agreed only weakly with the blind ones (kappa about 0.33), and are no longer used.

**Measures:**
- **Within one job:** do fits rank above non-fits?
- **Across jobs:** does a fit outscore other jobs' non-fits?
- **No match:** does a job's top score tell jobs with a fit from jobs with none?
- **Shortlist counts.**
- **Checks for noise:** cross-validation by job (fit on 4/5 of the jobs, test on the rest) and bootstrap intervals over jobs.

**Lesson:** several "wins" on one label sheet vanished on the next. Only results that held on held-out jobs and new labels were adopted.

## Adopted (in the pipeline)

| Change | Why |
|---|---|
| TF-IDF word overlap as a score part (`weights.lexical` 0.20) | Ranked fits better than the skill list on every measure, with no upkeep |
| Skill list frozen for ranking | It stays for explanations and type of work; TF-IDF covers vocabulary |
| `min_score` 0.45, bands at 0.60 (medium) and 0.70 (high) | On blind labels: 88% fits in high, 75% in medium, 29% in low, 4% below `min_score` |
| bge-m3 reads 1,024 tokens (was 512) | 35% of job texts were cut; costs about 15% more time |
| Search Keywords in the contractor text | User's choice; neutral on blind labels |
| Profession note for reviewers ([config/professions.yaml](../config/professions.yaml)) | "different profession" marks about 89% rejections; shown, never scored |
| Vocabulary for other fields (contract phrases, professions, industries, levels) | Postings outside marketing and tech are read correctly; no score change |

## Tried and not adopted

| Alternative | Result |
|---|---|
| Cross-encoder reranker | Slower, no gain. Removed |
| ColBERT multi-vector | No gain, 1–2 MB per job |
| bge-m3 sparse weights instead of TF-IDF | Mixed, then worse on blind labels |
| Raw-text second vector | Looked best after sheet 1, worse after sheet 2 |
| Sentence matching (MaxSim) | Boilerplate dominates |
| Facets (several vectors per contractor) | No reliable gain; useful only as an explanation |
| Combined-field vectors (role + industry, etc.) | 14 variants, all within noise |
| One-way "covers the job" evidence | Ranks worse than the whole-text vector |
| Profession mismatch as a score penalty | Cleaner shortlists but loses fits; worse on held-out jobs |
| Re-fitted weights (`tune.py`) | +0.006 on held-out jobs, shortlists not cleaner |
| Learned score (logistic regression over 17 features) | +0.017 after 2 sheets, +0.003 after the direct test on sheet 3 |

## Where v0 stands

Today's score:
- within a job, ranks fits above non-fits about 87% of the time, and across jobs about 90%;
- matches the AI judges about as well as they match each other (89% agreement).

**The limit:** "same domain, different profession" pairs (UX design for a UX writing job, sales for recruiting) still score high. Embeddings and word overlap can't see the difference. That's the job v1's LLM judge is meant to do.

## For v1

Compare any LLM judge against today's score on the same 331 blind pairs, with `scripts/eval/` (`common.py` has the measures, `label_sheet.py` makes and imports sheets). For a new sheet that samples where the two disagree, add the LLM's shortlist to `alternatives` in `label_sheet.py make`.
