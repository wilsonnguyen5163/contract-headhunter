from collections.abc import Callable

from jobmatch.normalize.dedupe import DedupeDecision, dedupe
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
