"""Full-time / part-time is recorded next to the employment type and never filters a job."""

from datetime import UTC, datetime

import pytest

from conftest import CONFIG_DIR
from jobmatch.config import load_contract_phrases
from jobmatch.normalize.commitment import CommitmentDetector
from jobmatch.normalize.pipeline import Normalizer
from jobmatch.schemas import Commitment, EmploymentType, JobStatus, RawJob

DETECTOR = CommitmentDetector(load_contract_phrases(CONFIG_DIR).commitment)
FULL, PART, UNKNOWN = Commitment.FULL_TIME, Commitment.PART_TIME, Commitment.UNKNOWN
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Full-time", FULL),
        ("Full Time Temporary (1-Year Contract)", FULL),
        ("FullTime", FULL),  # Ashby-style structured value
        ("Poste à temps plein", FULL),
        ("Temps complet", FULL),
        ("Part-time", PART),
        ("PartTime", PART),
        ("Contrat à temps partiel", PART),
        ("Mi-temps", PART),
        ("Full-time or part-time considered", FULL),  # first term seen
        ("37.5 hours per week", FULL),  # no term: hours decide
        ("20-25 hours a week", PART),
        ("35 h/semaine", FULL),
        ("Horaire de 21 heures par semaine", PART),
        ("6-month contract", UNKNOWN),
    ],
)
def test_detect(text: str, expected: Commitment) -> None:
    assert DETECTOR.detect(text) is expected


def test_order_structured_then_title_then_text() -> None:
    assert DETECTOR.detect("PartTime", "Analyst (Full-time)", "full-time role") is PART
    assert DETECTOR.detect(None, "Analyst", "Part-time, 3 days a week") is PART
    assert DETECTOR.detect(None, None, None) is UNKNOWN


def raw(**fields: object) -> RawJob:
    base: dict[str, object] = {
        "source": "manual",
        "source_id": "url-1",
        "url": "https://example.org/1",
        "title": "Business Analyst",
        "location": "Toronto, ON",
        "fetched_at": NOW,
    }
    base.update(fields)
    return RawJob(**base)


@pytest.mark.parametrize(
    ("fields", "employment", "commitment"),
    [
        ({"description": "Full-time, 6-month contract with possible extension."},
         EmploymentType.CONTRACT, FULL),
        ({"description": "Contrat à durée déterminée de 12 mois, temps plein."},
         EmploymentType.CONTRACT, FULL),
        ({"title": "Freelance Writer (Part-time)", "description": "Write weekly blog posts."},
         EmploymentType.CONTRACT, PART),
        ({"employment_type": "Contract, full-time", "description": "Lead the ERP rollout."},
         EmploymentType.CONTRACT, FULL),
        ({"employment_type": "FullTime", "description": "12-month fixed-term role."},
         EmploymentType.CONTRACT, FULL),
        ({"title": "HR Advisor (FTC, part-time)", "description": "Support the HR team."},
         EmploymentType.CONTRACT, PART),
        ({"employment_type": "FTC", "description": "Support the finance team."},
         EmploymentType.CONTRACT, UNKNOWN),
        ({"description": "Full-time role on our analytics team."},
         EmploymentType.UNKNOWN, FULL),  # no contract or permanent wording: kept for review
    ],
)  # fmt: skip
def test_full_or_part_time_contracts_are_kept_as_contract(
    normalizer: Normalizer,
    fields: dict[str, object],
    employment: EmploymentType,
    commitment: Commitment,
) -> None:
    job = normalizer.normalize(raw(**fields), now=NOW)
    assert (job.employment_type, job.commitment) == (employment, commitment)
    assert job.status is JobStatus.NEW


def test_permanent_full_time_is_still_filtered(normalizer: Normalizer) -> None:
    job = normalizer.normalize(raw(description="Permanent full-time position."), now=NOW)
    assert (job.employment_type, job.commitment) == (EmploymentType.PERMANENT, FULL)
    assert job.filter_reason == "not_contract"
