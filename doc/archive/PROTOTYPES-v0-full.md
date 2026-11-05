> **Archived 2026-11-04.** The full v0 experiment log, kept for reference. The summary is in [../PROTOTYPES.md](../PROTOTYPES.md). The experiment scripts named below were deleted when the prototype ended. Only `scripts/eval/` (label sheets, measures, `tune.py`) remains.

# Prototypes: alternatives to hand-written matching rules

Branch `dev/prototypes`, 2026-11-02. These are read-only experiments: nothing in the pipeline changed. Script: [scripts/prototypes/compare_methods.py](../scripts/prototypes/compare_methods.py).

## The question

Normalization relies on hand-written rules: a skill list with aliases and "not when" exceptions, seniority words, section headers and field patterns. Each new batch of postings needs new patches. Can we normalize and match without growing these rules?

Three alternatives were tested (a local LLM is out of scope):

1. **Sentence matching (MaxSim):** each sentence of the posting is compared with each item of the profile (headline, titles, strengths, proof points, summary sentences). The best match per sentence is kept, then averaged (all sentences, or the best 8). No skill list. Same `bge-m3` model.
2. **Cross-encoder reranker** (`BAAI/bge-reranker-v2-m3`): a second model reads the job and the profile together and scores the pair.
4. **TF-IDF word overlap:** shared words count more when they're rare across postings. No skill list. Pure Python, no new dependency.

Also measured:
- **Raw text,** to see what the parser contributes: the whole raw posting embedded against the whole raw profile, and raw titles against contractor titles.
- **Combinations** of the above, and of the above with the current score.

## How it was measured

- **Data:** 82 jobs × 27 contractors. **381 labelled pairs:** 59 fit, 122 partial, 200 no.
  - The labels include every pair the parser-free methods put in their top 3, so they aren't only the current system's own picks.
- **Ranking within a job (W):** how often a fit (or fit-or-partial) is scored above a non-fit for the same job. This measures the order of a shortlist.
- **Across jobs (G):** the same comparison over all jobs together. This decides whether one threshold (`min_score`) works for every posting.
- **No-match (NM):** whether the best score separates jobs that have a fit in our pool from jobs that have none.
- **Uncertainty:** 90% bootstrap intervals, resampling jobs.

All AUCs run from 0.5 (random) to 1.0 (perfect).

## Results

| Method | W fit>no | W fit>partial | W fit-or-partial>no | G fit>no | G fit-or-partial>no | NM | Time (dev PC) |
|---|---|---|---|---|---|---|---|
| **Current** (parser + skill list + rules) | **0.959** | 0.875 | 0.801 | 0.958 | 0.840 | 0.928 | 0.2 s scoring |
| Current: parsed-text cosine only | 0.878 | 0.808 | 0.690 | 0.877 | 0.756 | 0.872 | — |
| Current: title cosine only | 0.878 | 0.808 | 0.748 | 0.900 | 0.765 | 0.824 | — |
| Current: **skill-list overlap only** | 0.838 | 0.688 | 0.663 | 0.866 | 0.721 | 0.783 | — |
| Raw text, whole, embedded | 0.892 | **0.917** | 0.667 | 0.873 | 0.731 | 0.802 | 35 s |
| Raw titles, embedded | 0.797 | 0.771 | 0.617 | 0.790 | 0.653 | 0.768 | 3 s |
| (1) Sentence matching, raw, all sentences | 0.757 | 0.725 | 0.619 | 0.786 | 0.677 | 0.669 | 72 s |
| (1) Sentence matching, raw, best 8 | 0.824 | 0.733 | 0.619 | 0.859 | 0.728 | 0.778 | (same) |
| (1) Sentence matching, parsed sections, best 8 | 0.797 | 0.650 | 0.595 | 0.779 | 0.688 | 0.707 | 37 s |
| (2) Reranker, raw posting | 0.797 | 0.742 | 0.663 | 0.622 | 0.559 | 0.501 | 769 s (347 ms/pair) |
| (2) Reranker, parsed posting | 0.865 | 0.775 | 0.680 | 0.813 | 0.707 | 0.666 | 696 s (314 ms/pair) |
| (4) **TF-IDF, raw text** | 0.851 | 0.725 | 0.682 | 0.922 | 0.771 | 0.886 | **0.1 s** |
| Parser-free mix: raw text + sentences + title + TF-IDF | 0.905 | 0.883 | 0.687 | 0.918 | 0.764 | 0.881 | — |
| Current + reranker | 0.946 | 0.892 | 0.799 | 0.888 | 0.743 | 0.861 | — |
| Current + 0.5 TF-IDF | 0.959 | 0.842 | 0.816 | **0.966** | **0.850** | 0.933 | — |
| Current + 0.5 TF-IDF + 0.5 raw text | 0.959 | 0.875 | **0.820** | **0.966** | 0.841 | **0.936** | — |
| Current, **skill list swapped for TF-IDF** | 0.932 | 0.833 | 0.793 | 0.948 | 0.832 | 0.912 | — |

90% intervals for fit-or-partial vs no:

| Method | Within a job | Across jobs |
|---|---|---|
| Current | 0.750–0.856 | 0.814–0.868 |
| Current + 0.5 TF-IDF + 0.5 raw text | 0.778–0.863 | 0.817–0.869 |
| Current, skill list swapped for TF-IDF | 0.749–0.836 | 0.805–0.859 |
| Parser-free mix | 0.637–0.744 | 0.731–0.806 |
| TF-IDF alone | 0.633–0.738 | 0.736–0.807 |
| Skill-list overlap alone | 0.608–0.722 | 0.686–0.762 |

## What it means

1. **Removing the parser entirely would lose quality.** The best parser-free mix is clearly below the current system (0.64–0.74 vs 0.75–0.86 within a job). The parser's real value is **cleanup and structure**:
   - removing the employer pitch, benefits and EEO text;
   - the title;
   - seniority, for the gate.

   With boilerplate left in, the dense embedding is worse at "nobody in the pool fits" (0.80 vs 0.87), and the reranker can't do it at all (0.50).
2. **The skill list isn't needed for ranking.** On its own it's the weakest signal, and TF-IDF beats it on every measure with zero upkeep. Swapping it for TF-IDF inside the current score stays within the noise.
   - It is still useful for **explanations**: "matched skills" and "missing must-haves" shown to the reviewer.
   - A missing alias should cost a line of explanation, not a place in the ranking.
3. **Sentence matching (1)** doesn't pay off here. Boilerplate and generic sentences dominate the average. It's also 2× slower than whole-text embedding.
4. **The reranker (2)** doesn't fit this task.
   - It's trained to match a short question to a passage; here it reads two long documents cut at 512 tokens.
   - It's worse than the current system and can't tell jobs with a fit from jobs without one.
   - It costs 2.3 GB of disk, about 2.5 GB of RAM, and 0.35 s per pair on the dev PC. Expect 1–2 s per pair on the 2019 MacBook, or 30–60 s per job if it reranks 27 contractors.
5. **Raw-text embedding** is the best single signal for ordering fit vs partial (0.917). Added to the current score, it helps a little, but within the noise.

## Recommendations

1. **Keep the structural parser** (sections and boilerplate, title, seniority, pay, duration, location). Stop growing it by default: fix a rule only for a failure seen on real data, always with a test.
2. **Add TF-IDF word overlap to the score; freeze the skill list for ranking.**
   - The skill list stays for explanations and must-have notes.
   - Prototype as "current + 0.5 TF-IDF". If blind labels confirm it, move to "skill list swapped for TF-IDF", so ranking no longer depends on aliases at all.
   - Cost: under a second, no new dependency, no model.
3. **Optional, once blind labels exist:** add the raw-text embedding as a second vector. It costs a second embedding per job (about 0.5 s here, a few seconds on the Mac) and doubles vector storage.
4. **Don't adopt the reranker or sentence matching.** The reranker model downloaded for this test sits in the `hf-cache` Docker volume (about 2.3 GB) and can be removed.
5. **Most important: blind reviewer labels.** Every number above rests on Claude's labels, which aren't blind and are biased towards the current system's picks.
   - With 59 fits, differences under about 0.05 AUC are noise.
   - 150–200 pairs labelled by a reviewer who can't see the scores, spread across all bands, would settle items 2 and 3.

## Adopted (2026-11-02)

| Recommendation | What was done |
|---|---|
| 1. Keep the parser, stop growing it | Kept. Policy: fix a rule only for a real failure, with a test. |
| 2. TF-IDF in the score, skill list frozen for ranking | **Done, first step:** TF-IDF is a fifth score part (`weights.lexical: 0.2`, `lexical_ceiling: 0.10`, [match/lexical.py](../src/jobmatch/match/lexical.py)). The skill list keeps its weight for now and carries a freeze note. Removing it from the score waits for blind labels. |
| 3. Raw-text embedding | Not done: waits for blind labels. |
| 4. Drop reranker and sentence matching | Done: reranker code and model (2.3 GB) removed. Sentence matching stays only in the prototype script. |
| 5. Blind labels | Still the next step. |
| (earlier) Medium band from 0.60 | Done (`bands.medium: 0.60`). |

**Choosing the TF-IDF setup** (`scripts/prototypes/sweep_lexical.py`, with the real scorer):
- **Word rarity is counted over jobs and contractors,** not contractors only: 0.851 vs 0.797 within a job, alone.
- **Raw posting text:** cleaned text was no better.
- **Weight 0.2** was the best balance. Higher weights separate "nobody fits" slightly better but hurt the order within a job.

**Before and after, same 390 labels:**

| Score | W fit>no | W fit>partial | W fit-or-partial>no | G fit>no | G fit-or-partial>no | NM |
|---|---|---|---|---|---|---|
| Before | 0.959 | **0.875** | 0.801 | 0.958 | 0.840 | 0.928 |
| After | 0.960 | 0.835 | **0.828** | **0.966** | **0.850** | **0.938** |

**The trade-off:** TF-IDF is better at separating relevant contractors from irrelevant ones and at "nobody fits", but worse at putting a fit above a partial within a job. All differences are within the noise of these labels.

**Shortlists** (`min_score` 0.45):
- The same 55 fits, with 6 fewer non-fits.
- **High band:** 82% fit, no non-fits.
- **Medium band:** 41% fit (was 19% with medium from 0.50).
- **Low band:** 5% fit.

One human reviewer **A** pair (TAL-BC-006, 0.571) now reads **low**. It's still shortlisted.

## Dual vectors and longer context (2026-11-03)

[scripts/prototypes/dual_embedding.py](../scripts/prototypes/dual_embedding.py) tests two ideas on top of the adopted score:
- **A second vector** of the raw posting (dual embedding).
- **A longer `bge-m3` context** (`max_seq_length`), from 512 up to 8,192 tokens.

Score changes are emulated from the stored score parts. Labels: 390 pairs, Claude's, not blind.

**How much of the text the 512-token limit cuts off:**

| Text | Median tokens | Longest | Cut at 512 | Cut at 1,024 | Cut at 2,048 |
|---|---|---|---|---|---|
| Template (current job vector) | 440 | 948 | **35%** | 0% | 0% |
| Raw posting | 726 | 1,999 | **77%** | 33% | 0% |
| Contractor profiles | 100–130 | 344 | 0% | 0% | 0% |

**Cost on the dev PC** (CPU only, 4 GB container): the template vector takes 0.29 s per job at 512 tokens and 0.33 s from 1,024 up. The raw vector takes 0.35 s at 512, 0.58 s at 1,024 and 0.7 s at 2,048. Peak memory is 3.0 GB. Beyond 2,048 tokens nothing changes, because no posting is longer.

**Quality:**

| Variant | W fit>no | W fit>partial | W any>no | G any>no | NM | Shortlist fit/partial/no/unlabelled |
|---|---|---|---|---|---|---|
| Adopted score | 0.960 | 0.835 | 0.828 | 0.850 | 0.938 | 55/86/48/0 |
| Template vector at 1,024+ tokens | 0.960 | 0.835 | 0.825 | 0.849 | 0.938 | 55/83/48/2 |
| + raw vector 2,048 tok, weight 0.15 | 0.973 | 0.857 | 0.825 | 0.846 | 0.938 | 55/85/48/8 |
| + raw vector 2,048 tok, weight 0.30 | 0.947 | 0.880 | 0.815 | 0.843 | 0.939 | 56/81/57/16 |
| + raw vector 2,048 tok, weight 0.45 | 0.947 | 0.887 | 0.805 | 0.836 | 0.939 | 56/84/60/18 |

90% intervals, W fit>partial: adopted 0.77–0.90; with the raw vector at weight 0.30, 0.82–0.95. They overlap.

**Reading:**
- **A longer context for the template vector** stops 35% of postings being cut, for about 15% more time. On these labels the score doesn't change.
- **The raw vector recovers the fit-vs-partial loss TF-IDF caused** (0.835 → 0.880 at weight 0.30). The cost is about 9 more non-fits in shortlists and slightly worse fit-or-partial-vs-no. At weight 0.15 it costs nothing, but the gain (+0.02) is noise.
- **The raw vector needs 2,048 tokens:** at 512, 77% of raw postings are cut.

**Adopted:** `max_seq_length` is now 1,024. The limit is part of the stored model version, so changing it re-embeds everything. Shortlists: the same 55 fits and 48 non-fits; high band 85% fit. The raw-text vector isn't adopted (waits for blind labels).

## bge-m3 output modes (2026-11-03)

`bge-m3` gives three outputs from one forward pass:
- **dense:** what jobmatch stores today;
- **sparse:** learned weights per word, a "smarter TF-IDF";
- **ColBERT multi-vector:** one vector per token.

[scripts/prototypes/bge_m3_modes.py](../scripts/prototypes/bge_m3_modes.py) applies the two extra heads (`sparse_linear.pt`, `colbert_linear.pt`, about 4 MB, downloaded at the pinned revision) to the cached model. It doesn't install `FlagEmbedding` and changes no pinned package. Labels: 392 pairs, Claude's, not blind.

| Signal (alone) | W fit>no | W fit>partial | W any>no | G any>no | NM |
|---|---|---|---|---|---|
| Adopted full score, for reference | 0.960 | 0.836 | 0.819 | 0.848 | 0.938 |
| TF-IDF (adopted word overlap) | 0.853 | 0.687 | 0.694 | 0.773 | 0.887 |
| Dense, template | 0.867 | 0.806 | 0.680 | 0.750 | 0.873 |
| **Sparse, template** | **0.907** | 0.784 | **0.728** | 0.764 | 0.879 |
| ColBERT, template (profile→job) | 0.893 | 0.754 | 0.715 | 0.786 | 0.883 |
| **m3 mix, template** (0.4 dense / 0.2 sparse / 0.4 ColBERT) | 0.907 | 0.806 | **0.741** | **0.793** | **0.922** |
| m3 mix, raw text | 0.880 | 0.843 | 0.741 | 0.779 | 0.920 |

| Inside the adopted score | W fit>no | W fit>partial | W any>no | G any>no | NM | Shortlist f/p/n/unl |
|---|---|---|---|---|---|---|
| Adopted | 0.960 | 0.836 | 0.819 | 0.848 | 0.938 | 55/85/48/0 |
| TF-IDF swapped for sparse (template) | 0.947 | 0.873 | 0.777 | 0.838 | 0.939 | 55/78/48/9 |
| TF-IDF swapped for sparse (raw) | 0.960 | 0.858 | 0.764 | 0.843 | 0.948 | 55/76/42/10 |
| + ColBERT template, weight 0.15 | 0.960 | 0.836 | 0.832 | 0.852 | 0.941 | 55/84/51/12 |
| + m3 mix raw, weight 0.15 | 0.973 | 0.843 | 0.832 | 0.849 | 0.944 | 55/84/48/5 |

**Cost** (dev PC): one forward pass gives all three. 0.47 s per job for the template (1,024 tokens), 0.97 s for raw text (2,048 tokens). Peak memory 3.4 GB. ColBERT needs **about 1–2 MB per job** (73–147 MB for 82 jobs at float16), against 4 KB for a dense vector.

**Reading:**
- **Sparse beats TF-IDF as a word-overlap signal on its own.** Within a job it scores 0.907 vs 0.853 for fit>no and 0.784 vs 0.687 for fit>partial. It also handles EN/FR word forms and synonyms the model learned.
  - Swapped into the score, it helps fit>partial but hurts fit-or-partial>no.
  - The net effect is mixed and within noise.
- **The m3 mix** is the strongest model-only signal: 0.922 at "nobody fits", close to the full score with no title, skill list or rules. Inside the adopted score it moves nothing beyond noise.
- **ColBERT adds no measurable gain** and costs 1–2 MB per job. Rejected.

**Decision:** nothing adopted. The next candidates for a blind-label round are (a) sparse in place of TF-IDF, and (b) the m3 mix at a small weight. The two head files (about 4 MB) stay in the `hf-cache` volume.

## Search Keywords, facets and combined fields (2026-11-03)

Run after the contractor list grew from 27 to 38 (5 profiles re-joined after an ID fix in the workbook, 6 new talents) and Search Keywords joined the contractor text vector, the skill detection and the word overlap. Labels: 406 pairs, Claude's, not blind. 14 of them are on re-joined contractors and were made back when those contractors had thin profiles. The 11 new contractors have few or no labels, so shortlist counts now include many unlabelled pairs (61 of 205).

**The pipeline before and after:**

| | W fit>no | W fit>partial | W any>no | G any>no | NM | Shortlist f/p/n/unl |
|---|---|---|---|---|---|---|
| Before (27 contractors, no keywords, 392 labels) | 0.960 | 0.835 | 0.828 | 0.850 | 0.938 | 55/86/48/0 |
| After (38 contractors, with keywords) | 0.944 | 0.825 | 0.784 | 0.850 | 0.915 | 61/57/26/61 |
| After, keywords taken out of the text vector and word overlap | 0.944 | 0.838 | **0.814** | 0.852 | 0.914 | 62/60/27/60 |

Paired 90% interval of (with keywords - without): W fit>partial -0.023..+0.000, **W fit|partial>no -0.043..-0.019**. The interval excludes zero: on these labels **the keywords make ranking worse**. Not every point of the drop is due to the keywords: the extra labels and contractors move NM and the shortlist counts too. Possible reasons: keywords are short, generic search words ("marketing", "digital") that raise the similarity with many postings; and Claude labelled without seeing the keywords, which could bias the labels against them. Only blind labels can tell these apart.

**Facets** ([facets.py](../scripts/prototypes/facets.py)): several vectors per contractor, scored by the best one. There's no human facet sheet, so they are built from the workbook: (A) one per field group, (B) one per title with all skills, (C) one per title with each strength and keyword assigned to its closest title (median 2 per facet, 61 of 244 facets got none).

| Inside the adopted score | W fit>no | W fit>partial | W any>no | G any>no | NM | Shortlist f/p/n/unl |
|---|---|---|---|---|---|---|
| Adopted | 0.944 | 0.825 | 0.784 | 0.850 | 0.915 | 61/57/26/61 |
| A. field facets, best, replaces template | 0.921 | 0.857 | 0.771 | 0.852 | 0.928 | 61/66/39/70 |
| B. title facets, best, + weight 0.30 | 0.944 | 0.844 | 0.802 | 0.846 | 0.919 | 62/66/37/66 |
| C. title facets with assigned skills, best, + weight 0.30 | 0.944 | 0.838 | 0.808 | 0.845 | 0.917 | 61/64/35/70 |

Best case (C, + 0.30): W any>no +0.004..+0.043, W fit>partial -0.012..+0.036. It's a small gain, with 9 more non-fits in shortlists, and it was the best of 15 variants tried, so part of it is chance. As explanations, facets work: a contractor's winning title facet changes with the job for 24 of 38 contractors ("matched as: Data Analyst"). Field facets (A) rank worse: the keywords facet wins only 2% of pairs.

**Combined fields** ([feature_combos.py](../scripts/prototypes/feature_combos.py)): one extra vector per contractor from a few joined fields. Postings have no industry field, so each one is compared with the job template and with a short "title + skills" job text. Also one short sentence per role and industry ("Data Analyst in Healthcare"), scored by the best one.

| Signal alone | W fit>no | W fit>partial | W any>no | G any>no | NM |
|---|---|---|---|---|---|
| Template vector (today's text part) | 0.843 | 0.786 | 0.659 | 0.750 | 0.854 |
| Role + industry, vs job title+skills | 0.865 | 0.734 | 0.652 | 0.785 | 0.884 |
| Strengths + industry, vs job title+skills | 0.719 | 0.675 | 0.683 | 0.806 | 0.882 |
| Role + industry + strengths, vs job title+skills | 0.876 | 0.721 | 0.655 | 0.780 | 0.879 |
| Role + seniority + industry, vs job title+skills | **0.921** | 0.734 | 0.680 | 0.774 | 0.865 |
| Role x industry sentences, best, vs job template | 0.831 | 0.734 | 0.695 | 0.785 | 0.880 |

| Inside the adopted score | W fit>no | W fit>partial | W any>no | G any>no | NM | Shortlist f/p/n/unl |
|---|---|---|---|---|---|---|
| Adopted | 0.944 | 0.825 | 0.784 | 0.850 | 0.915 | 61/57/26/61 |
| Strengths + industry (vs job title+skills), replaces template | 0.944 | 0.825 | **0.817** | **0.872** | 0.909 | 63/71/39/74 |
| Role + seniority + industry (vs job template), replaces template | 0.944 | 0.812 | 0.805 | 0.842 | 0.913 | 59/62/36/72 |
| Role x industry sentences (vs job template), + weight 0.15 | 0.955 | 0.838 | 0.793 | 0.852 | 0.920 | 60/64/**25**/63 |
| Role + industry (vs job title+skills), + weight 0.15 | 0.955 | 0.838 | 0.787 | 0.851 | 0.915 | 60/66/27/67 |

Intervals (variant - adopted): strengths + industry W any>no +0.000..+0.061; role + seniority + industry +0.005..+0.040; role x industry sentences W fit>partial +0.000..+0.030. Each one only just clears zero, and 51 variants were tried, so a few are expected to clear it by chance.

**Reading:**
- **Keywords:** on these labels they hurt. Keep them only if blind labels say otherwise.
- **Facets and combined fields:** no variant is clearly better than the adopted score. Several are just as good, each in a different way, which is what noise looks like at 400 non-blind labels.
- **The one robust pattern:** short role-centred texts (role + seniority + industry) are strong on fit-vs-no, and weak on fit-vs-partial. That's the job the title vector already does.
- **Facets are worth keeping in mind for explanations,** less so for ranking.

**Decision:** nothing adopted; keywords stay in pending your call. Blind labels for the 11 new contractors are needed before any of this can be settled.

## More combined fields (2026-11-03, after re-tagging jobs)

Jobs were re-imported so they get the skills learned from the new profiles: 8 jobs gained tags (mostly "b2b"); no job changed status. The adopted score is now W fit>no 0.944, W fit>partial 0.828, W any>no 0.787, G 0.851, NM 0.919. With the keywords removed, W any>no is 0.811; the interval (-0.037..-0.012) still excludes zero, so **the keywords still hurt**.

[more_combos.py](../scripts/prototypes/more_combos.py) tests the seven other combinations. Two workbook columns that `build-contractors` doesn't import (Talent tab Skills / Notes, filled for 38 of 38; Recommended Buyers, 36 of 38) are read in memory. Industries were found in only 34 of 82 postings.

| Signal alone | W fit>no | W fit>partial | W any>no | G any>no | NM |
|---|---|---|---|---|---|
| Template vector (today's text part) | 0.843 | 0.786 | 0.662 | 0.751 | 0.857 |
| 1. Role + industry vs job title + industries found | 0.831 | **0.799** | 0.637 | 0.704 | 0.822 |
| 2. Role + proof points vs job template | 0.798 | 0.760 | 0.668 | 0.735 | 0.852 |
| 3. Keywords alone vs job title | **0.854** | 0.792 | 0.649 | 0.740 | 0.843 |
| 4. Strengths + keywords vs job requirements | 0.854 | 0.753 | 0.662 | 0.734 | 0.804 |
| 5. Primary Role + Skills / Notes vs job template | 0.787 | 0.727 | 0.637 | 0.752 | 0.820 |
| 6. Recommended Buyers + industry vs job title + industries | 0.742 | 0.636 | 0.659 | 0.706 | 0.751 |
| 7. Strength x industry sentences, best, vs job template | 0.697 | 0.610 | 0.622 | 0.742 | 0.805 |

| Inside the adopted score | W fit>no | W fit>partial | W any>no | G any>no | NM | Shortlist f/p/n/unl |
|---|---|---|---|---|---|---|
| Adopted | 0.944 | 0.828 | 0.787 | 0.851 | 0.919 | 61/58/25/64 |
| 1. Role + industry names, replaces template | 0.966 | 0.831 | 0.799 | 0.831 | 0.903 | 59/59/30/67 |
| 1. Role + industry, + weight 0.15 | 0.955 | 0.831 | 0.793 | 0.847 | 0.919 | 60/64/**23**/64 |
| 3. Keywords vs job title, + weight 0.15 | 0.944 | 0.851 | 0.787 | 0.851 | 0.923 | 61/61/29/62 |
| 6. Buyers + industry, + weight 0.15 | 0.955 | 0.825 | 0.796 | 0.852 | 0.922 | 61/64/29/65 |
| 7. Strength x industry sentences, + weight 0.30 | 0.955 | 0.825 | 0.799 | **0.859** | 0.917 | 60/67/30/62 |

All five best intervals include zero (W any>no from about -0.02 to +0.04).

**Reading:**
- **None of the seven beats the adopted score.**
- **Industry-based vectors are limited by the job side:** only 41% of postings name an industry we recognise.
- **Skills / Notes and Recommended Buyers add little as vectors.** They might still help as reviewer notes.
- **Keywords depend on how they're used.** Mixed into the profile text, they hurt. Used as a query against the job title only, they are among the best signals alone (W fit>no 0.854, above the template's 0.843), and at weight 0.15 they cost nothing. If keywords are kept, this is the place to try them, after blind labels.

**Decision:** nothing adopted.

## Blind labels: re-tuning and re-checking everything (2026-11-04)

**The labels.** The sheet came back with three judges on 155 pairs (62 jobs):
- a person labelled 13 (4 fit, 9 unfit);
- ChatGPT and Claude (web) labelled all 155, with reasons.

The person agreed with both AIs on all 13. The two AIs agree with each other on 89% (kappa 0.75). They agree only weakly with Claude Code's earlier non-blind labels (kappa 0.33), so those labels are no longer used for tuning.

Consensus (`data/eval/blind_consensus.csv`): the human's label when there is one, else the AIs' when they agree, else partial. That gives 40 fit, 98 no and 17 partial. The labels are fit/unfit, so "W fit>partial" now mostly reads 1.000 and says nothing.

**Caveats:**
- **Few comparisons within a job:** about 2.5 labelled pairs per job, so the within-job (W) measures rest on few pairs.
- **The sheet over-samples hard pairs:** 60 of 155 were picked where methods disagree.
- **Read the across-jobs measure (G) as the steadiest one.**

**Today's score on the blind labels:** W fit>no 0.919, G fit>no 0.927, NM 0.894. Shortlists hold 33 labelled fits, 2 partial and 25 no.

**Re-fitting the weights** ([tune.py](../scripts/prototypes/tune.py)): 432 combinations of the five weights, each scored through the real scorer, with 20 × 5-fold cross-validation by job.
- **In-sample, it looks like a big win:** text 0.60, title 0.15, skills 0.10, type of work 0, word overlap 0.30 gives W fit>no 1.000.
- **On held-out jobs, it loses.** Weights picked on 4/5 of the jobs scored worse than today's on the other 1/5 (mean -0.005, worse in 56% of folds, better in 36%).
- **Weights unchanged.**

**Bands and min_score**, by fit rate per score range with today's weights:

| Score | fit | partial | no | Fit rate |
|---|---|---|---|---|
| below 0.45 | 2 | 7 | 55 | 3% |
| 0.45-0.60 (low band) | 5 | 9 | 36 | 10% |
| 0.60-0.70 (medium) | 14 | 0 | 5 | 74% |
| 0.70+ (high) | 19 | 1 | 2 | 86% |

**What the table shows:**
- The 0.60 line (medium starts) is where fits begin, as set on 2026-11-02.
- **Medium and high are close** (74% vs 86%).
- **The low band is mostly misses:** 1 fit in 10.

Unchanged: raising `min_score` to 0.60 would drop 5 of 40 fits to remove 36 non-fits. That trade is a product decision.

**The pending options, re-checked on the blind labels:**

| Option | Result |
|---|---|
| Search Keywords out of the score | **Neutral** (within-job identical; G 0.841 vs 0.844). On the earlier labels they hurt; on blind labels they don't. Kept. |
| Raw-text vector, + weight 0.30 | W fit>no 0.946–0.973, G fit>no 0.941 (vs 0.927), NM 0.897. Better on **both** label sets, but adds 4 non-fits to the shortlists, and the intervals overlap (0.67–0.91 vs 0.71–0.92). The leading candidate. |
| Sparse instead of TF-IDF | Worse (W any>no 0.698–0.736 vs 0.774). Rejected. |
| bge-m3 mix (raw), + 0.15 | W fit>no 0.946, G any>no 0.855. Small, within noise. |
| Field facets, best, replaces template | W any>no +0.024..+0.094 (excludes zero), G any>no 0.871; +5 non-fits. |
| Strength x industry sentences, + 0.30 | W any>no +0.024..+0.094 (excludes zero), G any>no 0.864; +2 non-fits. On the earlier labels it was weak. |
| Primary Role + Skills / Notes, replaces template | W any>no 0.830, G any>no 0.861; interval touches zero. |
| Title facets, role + industry, keywords vs title, buyers | No gain. |

About 100 variants were compared, so a few intervals that exclude zero are expected by chance. Only the raw-text vector is better on both label sets.

**What the label reasons say about the mistakes.** The highest-scored "no" pairs are almost all **the same domain, different profession**:
- content marketing coordinator vs project coordinator / media planner;
- campaign producer vs graphic designer;
- HR manager vs a nonprofit operations director or a CFO;
- talent acquisition vs account executive;
- UX content strategist vs UX designer.

The text vectors see the shared domain words. The "type of work" part should catch the difference, but it works from skill categories, which miss the profession.

The lowest-scored "fit" pairs are adjacent roles the judges accepted: a controller role for a fractional CFO, a senior producer role for a content writer, a graphic designer role for a creative director. Those are partly judgment calls.

**Decision:**
- Settings unchanged (comments added to `settings.yaml`).
- The next candidates are:
  - (a) the raw-text vector at 0.30;
  - (b) a **profession check**: map each job title and contractor title to a small list of professions (HR, finance, design, content, project management, marketing, sales, data…) and flag or lower a mismatch.
- Both need a user decision and a fresh label sheet that includes the 11 new contractors.

## Profession check and one-way evidence (2026-11-04)

These copy two steps of how the LLM judges read a posting. They name the job's **profession** (what the person is accountable for, not the domain), and they check that the profile **covers** the job's core lines, rather than that the two texts resemble each other.

[professions.py](../scripts/prototypes/professions.py) assigns each job and each contractor title to the closest of 31 professions in the draft [config/professions.yaml](../config/professions.yaml). It uses bge-m3 similarity to a one-line description of each profession. Labels: 155 blind consensus pairs.

**Profession agrees with the judges:**

| Label | Same profession | Different profession |
|---|---|---|
| fit (40) | **31** | 9 |
| partial (17) | 9 | 8 |
| no (98) | 19 | **79** |

Jobs are assigned from the title plus the first 3 responsibilities, which beats the title alone (fit/same 31 vs 28).
- **Mostly right:** 81% of rejected pairs are a different profession, and 78% of fits are the same.
- **The 9 fits a mismatch rule would wrongly hit** are mostly *adjacent* professions the judges accept: creative director ↔ graphic designer or copywriter, content writer ↔ producer. Plus a few wrong assignments: "copywriter" became creative direction because its responsibilities read like concept work.
- **Some contractor titles are assigned badly:** generic "consultant" or "advisor" titles drift to "training and facilitation", and an "account manager" title drifts to finance.

| Inside the adopted score | W fit>no | W any>no | G any>no | NM | Shortlist f/p/n/unl |
|---|---|---|---|---|---|
| Adopted | 0.919 | 0.774 | 0.841 | 0.894 | 33/2/25/148 |
| Title profession, mismatch -0.10, when clear (margin ≥ 0.02) | 0.973 | 0.849 | 0.858 | 0.891 | 32/3/22/132 |
| Title profession, mismatch -0.10, always | 0.946 | 0.830 | 0.876 | 0.888 | 31/3/**17**/122 |
| Title + core lines, mismatch -0.15, always | 0.919 | 0.792 | **0.882** | 0.888 | 32/4/**14**/108 |
| Title + core lines, soft fit + 0.15 | 0.973 | 0.811 | 0.856 | 0.890 | 34/7/30/148 |
| One-way evidence (2 weakest lines) + 0.15 | 0.919 | 0.774 | 0.839 | 0.883 | 34/3/28/148 |

- **Intervals:** the best penalty variants have W any>no +0.022..+0.133.
- **Cross-validation:** picking the best of these 22 variants on 4/5 of the jobs and testing on the rest gives a held-out mean of **-0.013**. Better in 44% of folds, worse in 43%. So **no single variant is reliably better at ranking.**
- **Shortlists:** the penalty clearly cleans them. A -0.10 penalty removes 8 of 25 non-fits and loses 2 of 33 fits.

**One-way evidence doesn't work with embeddings.** "Weakest core line" alone scores 0.716, against 0.892 for the template. A job's core lines include generic ones ("collaborate with stakeholders") that nobody's profile item matches closely.

**Reading:**
- **Profession is the right concept.** It sorts the judges' fits from their rejections better than any part of today's score.
- **As a score penalty, it isn't proven.** It trades a couple of fits for cleaner shortlists, and the best penalty isn't stable across jobs.
- **The draft list needs two fixes before any rule uses it:**
  - **adjacency:** creative direction, graphic design and copywriting accept each other;
  - **title cleanup:** generic "consultant", "advisor" and "lead" titles shouldn't decide a profession.

**Decision:** nothing adopted. Recommended next step:
- Show the profession as a **reviewer note**, with no score change ("job: recruiting · talent: sales"), after the list is reviewed and adjacency is added.
- Reviewers' approve/reject decisions then show whether a penalty is worth it.

**Follow-up (same day): adopted as a note only.**
- **What changed:** `close` pairs were added, and contractor titles now count only when their profession is clear. Production uses cleaned titles.
- **Result:** on the 155 blind labels, "different" covers **2 fits and 53 non-fits** (the prototype hit 9 fits).
- **`min_margin` is 0.01:**
  - at 0.02, 23 of 82 jobs were "unclear"; at 0.01 it's 10;
  - "different" still covers only 2 fits.
- **Scores are unchanged.** The profession penalty is one of the methods compared on label sheet 2.

## Label sheet 2: re-checking on 305 blind pairs (2026-11-04)

**The sheet.** 150 pairs: 82 on the 11 contractors added on 2026-11-03, and 49 where today's open candidates disagree.
- **ChatGPT judged fit/unfit only:** it used its old prompt, and its answers landed one column to the left.
- **Claude used fit/partial/no** and said "partial" 55 times.
- **On paper they agreed on 57%.** Most of the gap is ChatGPT "fit" vs Claude "partial" (45 pairs).
- **User decision:** one AI's "fit" with the other's "partial" counts as fit (`ai-fit-partial` in the consensus). Counted that way, the AIs agree on 87%, close to sheet 1's 89%.
- **No person labelled this sheet.**

Consensus over both sheets: **305 pairs: 98 fit, 170 no, 37 partial** (sheet 1 alone had 40 fits).

**Today's score:** within one job, fit>no 0.874; across jobs 0.902; NM 0.938. Shortlists hold 80 labelled fits, 10 partial and 38 no. The score is lower than on sheet 1 alone: sheet 2 is harder, and its "fit" is broader (it includes Claude's partials).

**Bands.** Fit rate by score range, today's weights:

| Score | Fit rate |
|---|---|
| below 0.45 | 4% |
| 0.45-0.60 (low) | 29% |
| 0.60-0.70 (medium) | 75% |
| 0.70+ (high) | 88% |

Raising `min_score` to 0.60, the open question from sheet 1, would now drop **30 of 98 fits**. Keep 0.45.

**Re-fitting the weights** ([tune.py](../scripts/prototypes/tune.py)): 432 combinations, 20 × 5-fold cross-validation by job.
- **More stable than on sheet 1:** one combination was picked in 62 of 100 folds: text 0.30, title 0.15, skills 0.10, type of work 0.10, word overlap 0.30.
- **But the gain is small:** on held-out jobs it beats today's weights by +0.006 on average (median +0.011), better in 61% of folds and worse in 38%.
- **Shortlists:** on all labels, 78/16/40 instead of 80/10/38, so no cleaner.
- **Not adopted.** It's the first positive cross-validation result; worth re-checking after the next sheet.

**Profession note** (production version, `min_margin` 0.01):

| Label | same | close | different | unclear |
|---|---|---|---|---|
| fit (98) | 63 | 11 | 13 | 11 |
| no (170) | 26 | 19 | **102** | 23 |

"different" is still a strong warning: 102 of its 115 labelled pairs are rejections. That's 89%, against 96% on sheet 1 alone.

As a score penalty, cross-validation is negative again: mean -0.010, better in 44% of folds, worse in 55%. A -0.10 penalty removes about 12 non-fits from shortlists, but also drops 4–10 fits. **It stays a note.**

**Candidates that looked significant on sheet 1 alone, now with both sheets:**

| Candidate | Sheet 1 alone | Both sheets |
|---|---|---|
| Strength × industry sentences, + 0.30 | W any>no +0.024..+0.094 | 0.785 vs 0.785: **no effect** |
| Primary Role + Skills / Notes, replaces template | 0.830 | 0.805 vs 0.785; interval touches zero |
| Strengths + keywords vs job requirements | no gain | W any>no +0.007..+0.050, but W fit>partial falls (0.795) |
| Raw-text vector, + 0.30 | better on both earlier label sets | **worse**: W fit>no 0.852 vs 0.874; shortlists 77/16/43 vs 80/10/38. Rejected |

**This is why we wait for more labels:** the raw-text vector, the leading candidate after sheet 1, and the "significant" strength × industry result both disappeared on sheet 2.

**Decision:** nothing adopted. The note, today's weights and the bands stay.

## Option 3: a score learned from the labels (2026-11-04)

[learned_score.py](../scripts/prototypes/learned_score.py) learns how to combine the match signals from the 305 blind consensus labels, instead of using hand-picked weights. It's plain logistic regression in NumPy.
- **Targets:** fit 1, partial 0.5, no 0.
- **Features (17):**
  - today's five parts, each with a "missing" flag;
  - the profession verdict;
  - the seniority gap, under- and over-qualified, in levels;
  - missing must-haves;
  - same industry.
- **How it's checked:** cross-validation by job, 20 × 5 folds. The L2 strength is picked inside each training fold, and every comparison is on jobs the model never saw.

| Model | Held-out gain vs today (mean / median) | Better in | Worse in |
|---|---|---|---|
| 1. Today's five parts, learned weights | +0.009 / +0.008 | 60% | 34% |
| 2. Every feature | +0.017 / +0.022 | 69% | 30% |
| 3. Every feature, penalties may only lower the score | +0.017 / +0.019 | **70%** | 29% |

One repeat's held-out scores (every job scored by a model that never saw its labels):

| | W fit>no | W fit>partial | W any>no | G any>no | NM | Shortlist f/p/n/unl, same size as today |
|---|---|---|---|---|---|---|
| Today's score | 0.874 | 0.846 | 0.785 | 0.853 | 0.938 | 80/10/38/80 |
| Model 3 | **0.896** | **0.885** | **0.832** | 0.848 | 0.922 | 74/11/34/89 |

Intervals vs today: W fit>partial +0.000..+0.087, W any>no -0.012..+0.099.

**What the labels reward** (model 3, per standard deviation):

| Feature | Weight |
|---|---|
| words (TF-IDF) | +0.71 |
| text | +0.49 |
| title | +0.43 |
| type of work | +0.32 |
| same profession | +0.31 |
| shared skills | +0.24 |
| different profession | **-0.23** |
| over-qualified | **-0.21** |

**Reading:**
- **The best held-out result of anything tried.** Re-fitting today's weights (`tune.py`) gave +0.006 and was better in 61% of folds. Model 3 gives +0.017 and is better in 70%.
- **The ranking within a job improves the most.** Fit vs partial goes from 0.846 to 0.885, and fit-or-partial vs no from 0.785 to 0.832. That's where the profession and seniority features help.
- **Not proven yet:**
  - **Intervals touch zero.**
  - **The shortlist comparison is muddied.** Model 3 swaps about 9 labelled pairs (6 fits, 4 non-fits) for unlabelled ones, so its shortlists can't be judged on these labels.
  - **NM drops slightly,** from 0.938 to 0.922.
- **Some learned weights look like sampling artefacts, not real effects:**
  - "same industry" comes out at -0.20;
  - "no skills to compare" at +0.29;
  - "under-qualified" at +0.13 in model 2, which model 3 holds at 0.

  The sheets over-sample hard pairs. With more labels these should shrink.

**Decision:** nothing adopted. This is the leading candidate.

**Suggested next step:** a small label sheet (about 60–80 pairs) of exactly the pairs model 3 would put in shortlists that have no label yet. That tests its shortlists directly, then the cross-validation is re-run.

**Sheet 3 result (same day, Claude labels only; ChatGPT pending).** Sheet 3 holds the 11 unlabelled pairs where model 3's shortlists and today's differ, plus 15 both share.

| Pairs | fit | partial | no |
|---|---|---|---|
| Only model 3 shortlists (8) | 0 | 3 | 5 |
| Only today's score shortlists (3) | 0 | 0 | 3 |

Re-run on all 331 labels:

| Model | Held-out gain (mean / median) | Better in |
|---|---|---|
| 1. Learned weights | -0.001 / -0.000 | 48% |
| 3. Every feature, penalties only lower | **+0.003** / +0.010 | 63% |

With one repeat's held-out scores, model 3 is no better than today's score: W fit>no 0.872 vs 0.877. Its shortlists hold 76 fits and 47 non-fits, against today's 82 and 46.

**The earlier +0.017 didn't survive the direct test.** Part of it came from how sheets 1 and 2 were sampled. Some of the bias is expected: sheet 3 holds exactly the pairs where model 3 departs from today's score, the hardest test for it. But on those pairs its additions were no better than today's choices.

**Decision: not adopted.** Re-check once ChatGPT's labels for sheet 3 are in. Under option A, its "fit" could turn Claude's 3 partials into fits, which would bring the two shortlists roughly level, not make model 3 better.

**If adopted later:**
- the weights would live in a config file with their training date and label count;
- the score shown to reviewers would be the model's probability;
- the explanation would list each feature's contribution, which stays data-only as CLAUDE.md requires.

## Re-running

```
docker compose run --rm -v ./scripts/prototypes:/exp:ro app python /exp/compare_methods.py
docker compose run --rm -v ./scripts/prototypes:/exp:ro app python /exp/sweep_lexical.py
docker compose run --rm -v ./scripts/prototypes:/exp:ro app python /exp/dual_embedding.py   # ~5 min
docker compose run --rm -v ./scripts/prototypes:/exp:ro app python /exp/bge_m3_modes.py     # ~3 min
docker compose run --rm -v ./scripts/prototypes:/exp:ro app python /exp/facets.py           # ~1.5 min
docker compose run --rm -v ./scripts/prototypes:/exp:ro app python /exp/feature_combos.py   # ~1.5 min
docker compose run --rm -v ./scripts/prototypes:/exp:ro app python /exp/more_combos.py      # ~1.5 min
docker compose run --rm -e LABELS=data/eval/blind_consensus.csv -v ./scripts/prototypes:/exp:ro app python /exp/tune.py   # ~3 min
docker compose run --rm -e LABELS=data/eval/blind_consensus.csv -v ./scripts/prototypes:/exp:ro app python /exp/professions.py   # ~1 min
docker compose run --rm -e LABELS=data/eval/blind_consensus.csv -v ./scripts/prototypes:/exp:ro app python /exp/learned_score.py   # ~2 min
```

`bge_m3_modes.py` needs `-e HF_HUB_OFFLINE=0` on its first run only, to fetch the two head files.

Read-only: they write nothing to `data/` or `exports/`. Each takes about 3 minutes on the dev PC. The reranker was removed from the script after testing; its results above are from the 2026-11-02 run.
