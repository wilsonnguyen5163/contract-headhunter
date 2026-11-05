"""TF-IDF word overlap (match/lexical.py) and its place in the score. Synthetic texts only."""

import pytest

from jobmatch.config import load_settings
from jobmatch.match.lexical import LexicalIndex, contractor_lexical_text, job_lexical_text
from jobmatch.match.scorer import ContractorFacts, JobFacts, score_pair
from jobmatch.schemas import ContractorProfile, Job


def test_tokens_fold_case_and_accents_and_drop_stopwords() -> None:
    index = LexicalIndex([], stopwords=["the", "avec"])
    assert index.tokens("The Rédactrice works WITH Power BI, avec C# and .NET.") == [
        "redactrice", "works", "with", "power", "bi", "c#", "and", "net",
    ]  # fmt: skip


def test_a_shared_rare_word_counts_more_than_a_shared_common_word() -> None:
    docs = ["marketing manager webflow", "marketing coordinator", "copywriter", "data analyst"]
    index = LexicalIndex(docs)
    sims = index.similarity(["marketing manager webflow"], ["webflow designer", "marketing lead"])
    assert sims[0, 0] > sims[0, 1] > 0
    # A word in every document says nothing: it carries no weight.
    everywhere = LexicalIndex(["marketing a", "marketing b", "marketing c"])
    assert everywhere.similarity(["marketing a"], ["marketing z"])[0, 0] == 0.0


@pytest.mark.parametrize(
    ("job", "close", "far"),
    [
        (
            "Rédacteur ou rédactrice web, contenu SEO et infolettres",
            "Rédactrice SEO, contenu web et infolettres pour marques",
            "Directeur financier, budgets et audit",
        ),
        (
            "Senior graphic designer: brand identity, typography, InDesign",
            "Graphic designer and art director: typography and brand identity",
            "Payroll administrator, year-end and T4 slips",
        ),
    ],
)
def test_english_and_french_postings_rank_the_closer_profile_first(
    job: str, close: str, far: str
) -> None:
    index = LexicalIndex([job, close, far])
    sims = index.similarity([job], [close, far])
    assert sims[0, 0] > sims[0, 1]


def test_no_shared_words_scores_zero_and_scores_stay_between_zero_and_one() -> None:
    index = LexicalIndex(["alpha beta", "gamma delta", "alpha beta gamma"])
    sims = index.similarity(["alpha beta"], ["gamma delta", "alpha beta"])
    assert sims[0, 0] == 0.0
    assert 0.99 < sims[0, 1] <= 1.0 + 1e-6


def test_fingerprint_changes_when_the_corpus_does() -> None:
    a = LexicalIndex(["data analyst", "copywriter"])
    assert a.fingerprint == LexicalIndex(["data analyst", "copywriter"]).fingerprint
    assert a.fingerprint != LexicalIndex(["data analyst", "copywriter", "seo"]).fingerprint


def test_texts_use_the_raw_posting_and_the_whole_profile() -> None:
    job = Job(
        job_id="manual:x",
        source="manual",
        source_id="x",
        url="https://example.org/x",
        title_raw="Content Editor",
        description_raw="About us: we sell shoes. You will edit web copy.",
        description_clean="You will edit web copy.",
        first_seen_at="2026-11-02T00:00:00Z",
    )
    assert (
        job_lexical_text(job) == "Content Editor\nAbout us: we sell shoes. You will edit web copy."
    )
    profile = ContractorProfile(
        talent_id="TAL-X",
        talent_name="Sample",
        standardized_headline="Copywriter",
        executive_summary="SYNTHETIC. Writes web copy.",
        core_strengths=["Copywriting", "Editing"],
        target_titles=["Content Editor"],
        search_keywords=["rédaction web", "SEO"],
        rate_expectation="60",
    )
    text = contractor_lexical_text(profile)
    assert "Copywriter" in text and "Copywriting; Editing" in text and "Sample" not in text
    assert "rédaction web; SEO" in text


def _facts() -> tuple[JobFacts, ContractorFacts]:
    job = Job(
        job_id="manual:x",
        source="manual",
        source_id="x",
        url="https://example.org/x",
        title_raw="Content Editor",
        first_seen_at="2026-11-02T00:00:00Z",
    )
    profile = ContractorProfile(
        talent_id="TAL-X",
        talent_name="Sample",
        standardized_headline="Copywriter",
        executive_summary="SYNTHETIC.",
        core_strengths=["Copywriting"],
        rate_expectation="60",
    )
    return JobFacts(job=job, must_have=()), ContractorFacts(
        profile=profile, skills=frozenset(), levels=()
    )


def test_word_overlap_moves_the_score_only_when_weighted() -> None:
    matching = load_settings().matching
    job, contractor = _facts()
    off = matching.model_copy(
        update={"weights": matching.weights.model_copy(update={"lexical": 0.0})}
    )
    assert score_pair(job, contractor, 0.6, off, lexical=0.10).score == (
        score_pair(job, contractor, 0.6, off, lexical=0.0).score
    )
    on = matching.model_copy(
        update={"weights": matching.weights.model_copy(update={"lexical": 0.2})}
    )
    high = score_pair(job, contractor, 0.6, on, lexical=matching.lexical_ceiling)
    low = score_pair(job, contractor, 0.6, on, lexical=matching.lexical_floor)
    assert high.score > low.score
    assert high.lexical == round(matching.lexical_ceiling, 4)
    assert score_pair(job, contractor, 0.6, on).lexical is None  # no index: part left out
