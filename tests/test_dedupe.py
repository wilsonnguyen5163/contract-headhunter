from collections.abc import Callable

import pytest

from jobmatch.normalize.dedupe import DedupeDecision, TitleSimilarity, dedupe
from jobmatch.schemas import Job, JobStatus


def test_new_jobs_pass_through(make_job: Callable[..., Job]) -> None:
    job = make_job(fingerprint="fp1", description_clean="Contrat de 6 mois")
    result = dedupe([], [job])
    assert result.to_add == [job]
    assert [o.decision for o in result.outcomes] == [DedupeDecision.NEW]


def test_same_source_and_id_is_already_stored(make_job: Callable[..., Job]) -> None:
    stored = make_job(fingerprint="fp1", description_clean="text")
    result = dedupe([stored], [make_job(fingerprint="fp1", description_clean="text v2")])
    assert result.to_add == [] and result.to_replace == []
    assert result.outcomes[0].decision is DedupeDecision.ALREADY_STORED


def test_same_url_twice_in_one_batch(make_job: Callable[..., Job]) -> None:
    job = make_job(fingerprint="fp1", description_clean="text")
    result = dedupe([], [job, job])
    assert len(result.to_add) == 1
    assert [o.decision for o in result.outcomes] == [
        DedupeDecision.NEW,
        DedupeDecision.ALREADY_STORED,
    ]


def test_same_fingerprint_from_another_posting_is_a_duplicate(
    make_job: Callable[..., Job],
) -> None:
    stored = make_job(fingerprint="fp1", description_clean="text")
    other = make_job(
        job_id="manual:url-abc", source="manual", source_id="url-abc", fingerprint="fp1",
        description_clean="same job elsewhere",
    )  # fmt: skip
    result = dedupe([stored], [other])
    [added] = result.to_add
    assert added.status is JobStatus.FILTERED_OUT
    assert added.filter_reason == f"duplicate:{stored.job_id}"
    assert result.outcomes[0].other_job_id == stored.job_id


def test_stub_is_filled_by_same_url_with_text(make_job: Callable[..., Job]) -> None:
    stub = make_job(status=JobStatus.NEEDS_DETAILS, fingerprint="fp1")
    full = make_job(
        fingerprint="fp1",
        description_clean="Full posting text",
        first_seen_at=stub.first_seen_at.replace(day=15),
    )
    result = dedupe([stub], [full])
    [replaced] = result.to_replace
    assert replaced.description_clean == "Full posting text"
    assert replaced.first_seen_at == stub.first_seen_at  # first sighting kept
    assert result.outcomes[0].decision is DedupeDecision.FILLS_STUB


def test_stub_superseded_by_full_posting_from_other_source(
    make_job: Callable[..., Job],
) -> None:
    stub = make_job(
        job_id="manual_linkedin:401", source="manual_linkedin", source_id="401",
        status=JobStatus.NEEDS_DETAILS, fingerprint="fp1",
    )  # fmt: skip
    full = make_job(fingerprint="fp1", description_clean="Full text")
    result = dedupe([stub], [full])
    assert result.to_add == [full]
    [old_stub] = result.to_replace
    assert old_stub.status is JobStatus.FILTERED_OUT
    assert old_stub.filter_reason == f"duplicate:{full.job_id}"
    assert result.outcomes[0].decision is DedupeDecision.SUPERSEDES_STUB


def test_filtered_jobs_are_not_used_as_originals(make_job: Callable[..., Job]) -> None:
    rejected = make_job(
        status=JobStatus.FILTERED_OUT, filter_reason="not_contract", fingerprint="fp1"
    )
    other = make_job(
        job_id="manual:url-x", source="manual", source_id="url-x", fingerprint="fp1",
        description_clean="text",
    )  # fmt: skip
    result = dedupe([rejected], [other])
    assert result.to_add == [other]
    assert result.outcomes[0].decision is DedupeDecision.NEW


# ---- similar titles (rapidfuzz) ----

SIMILARITY = TitleSimilarity(92, {"sr": "senior", "eng": "engineer", "&": "and"})


def job(make_job: Callable[..., Job], n: int, title: str, **fields: object) -> Job:
    base: dict[str, object] = {
        "job_id": f"manual:url-{n}",
        "source": "manual",
        "source_id": f"url-{n}",
        "title_norm": title,
        "company_norm": "example insurance",
        "city": "Toronto",
        "seniority": "senior",
        "fingerprint": f"fp-{n}",  # different fingerprints: only the fuzzy rule can match
        "description_clean": "text",
    }
    base.update(fields)
    return make_job(**base)


def test_abbreviated_title_at_same_company_is_a_duplicate(make_job: Callable[..., Job]) -> None:
    stored = job(make_job, 1, "senior data engineer")
    result = dedupe([stored], [job(make_job, 2, "sr. data eng.")], similarity=SIMILARITY)
    [outcome] = result.outcomes
    assert outcome.decision is DedupeDecision.DUPLICATE
    assert (outcome.other_job_id, outcome.similar_title) == (stored.job_id, True)


@pytest.mark.parametrize(
    ("title", "fields"),
    [
        ("data analyst", {"seniority": None}),  # "Data Analyst" vs "Senior Data Analyst"
        ("senior data engineer", {"city": "Ottawa"}),  # same title, other city
        ("senior data engineer", {"company_norm": "other co"}),
        ("senior data analyst", {}),  # a different role at the same company
        ("senior data engineer", {"company_norm": None}),  # no company: no fuzzy matching
    ],
)
def test_similar_rule_needs_same_company_city_seniority(
    make_job: Callable[..., Job], title: str, fields: dict[str, object]
) -> None:
    stored = job(make_job, 1, "senior data engineer")
    result = dedupe([stored], [job(make_job, 2, title, **fields)], similarity=SIMILARITY)
    assert result.outcomes[0].decision is DedupeDecision.NEW


def test_similar_title_completes_a_stub_from_another_source(make_job: Callable[..., Job]) -> None:
    stub = job(make_job, 1, "senior data engineer", status=JobStatus.NEEDS_DETAILS)
    full = job(make_job, 2, "sr data engineer")
    result = dedupe([stub], [full], similarity=SIMILARITY)
    assert result.outcomes[0].decision is DedupeDecision.SUPERSEDES_STUB
    assert result.to_replace[0].filter_reason == f"duplicate:{full.job_id}"


def test_without_similarity_only_exact_fingerprints_match(make_job: Callable[..., Job]) -> None:
    stored = job(make_job, 1, "senior data engineer")
    result = dedupe([stored], [job(make_job, 2, "sr. data eng.")])
    assert result.outcomes[0].decision is DedupeDecision.NEW


def test_title_key_expands_abbreviations() -> None:
    assert SIMILARITY.title_key("Sr. Data Eng. & Analyst") == "senior data engineer and analyst"
