"""End-to-end RawJob -> Job tests with synthetic English, French and bilingual postings."""

from datetime import UTC, datetime
from typing import Any

from conftest import posting
from jobmatch.normalize.fingerprint import job_fingerprint
from jobmatch.normalize.pipeline import Normalizer
from jobmatch.schemas import EmploymentType, Job, JobStatus, Language, RawJob, RemoteType

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def raw_job(**overrides: Any) -> RawJob:
    fields: dict[str, Any] = {
        "source": "adzuna",
        "source_id": "4471001",
        "url": "https://example.org/jobs/adzuna/4471001",
        "title": "Senior Python Data Engineer (Contract) - Req #A1029",
        "company": "Example Financial Inc.",
        "location": "Toronto, ON (Hybrid)",
        "description": posting("en_contract_data_engineer.html"),
        "posted_at": datetime(2026, 10, 14, 13, 5, tzinfo=UTC),
        "fetched_at": datetime(2026, 10, 14, 13, 20, tzinfo=UTC),
    }
    fields.update(overrides)
    return RawJob(**fields)


def test_english_html_contract_posting(normalizer: Normalizer) -> None:
    job = normalizer.normalize(raw_job(), now=NOW)
    assert job.job_id == "adzuna:4471001"
    assert job.title_norm == "senior python data engineer"
    assert job.company_norm == "example financial"
    assert (job.city, job.province, job.country) == ("Toronto", "CA-ON", "CA")
    assert job.remote_type is RemoteType.HYBRID
    assert job.employment_type is EmploymentType.CONTRACT
    assert job.duration_months == 6.0
    assert job.language is Language.EN
    assert job.seniority == "senior"
    assert {"python", "etl", "aws", "sql", "snowflake", "power bi", "airflow"} <= set(job.skills)
    assert job.requirements[0] == "5+ years of Python experience"
    assert job.description_clean is not None
    assert "fictional lender" not in job.description_clean
    assert "alert(" not in (job.description_raw or "")  # script removed, text kept as data
    assert job.fingerprint == job_fingerprint("example financial", job.title_norm, "Toronto")
    assert (job.status, job.filter_reason) == (JobStatus.NEW, None)
    assert job.first_seen_at == datetime(2026, 10, 14, 13, 20, tzinfo=UTC)


def test_french_contract_posting(normalizer: Normalizer) -> None:
    job = normalizer.normalize(
        raw_job(
            source="jobbank",
            source_id="48213377",
            title="Analyste d'affaires principal(e) – contrat de 12 mois",
            company="Organisme Exemple du Québec",
            location="Montréal (QC)",
            description=posting("fr_contrat_analyste.txt"),
        ),
        now=NOW,
    )
    assert job.title_norm == "analyste d'affaires principal"
    assert (job.city, job.province) == ("Montréal", "CA-QC")
    assert job.language is Language.FR
    assert job.employment_type is EmploymentType.CONTRACT
    assert job.contract_signals == ["contrat", "durée déterminée"]
    assert job.duration_months == 12.0
    assert job.seniority == "senior"
    assert job.skills[:3] == ["business analysis", "process mapping", "change management"]
    assert job.status is JobStatus.NEW


def test_bilingual_remote_posting(normalizer: Normalizer) -> None:
    job = normalizer.normalize(
        raw_job(
            title="IT Project Manager / Gestionnaire de projet TI",
            location="Remote - Canada / Télétravail - Canada",
            description=posting("bilingual_project_manager.txt"),
        ),
        now=NOW,
    )
    assert job.language is Language.BILINGUAL
    assert (job.city, job.province, job.country) == (None, None, "CA")
    assert job.remote_type is RemoteType.REMOTE
    assert job.employment_type is EmploymentType.CONTRACT
    assert "project management" in job.skills
    assert job.status is JobStatus.NEW


def test_permanent_posting_is_filtered(normalizer: Normalizer) -> None:
    job = normalizer.normalize(
        raw_job(
            title="Contract Manager, Procurement",
            location="Calgary, Alberta",
            description=posting("en_permanent_contract_manager.txt"),
        ),
        now=NOW,
    )
    assert job.employment_type is EmploymentType.PERMANENT
    assert (job.status, job.filter_reason) == (JobStatus.FILTERED_OUT, "not_contract")


def test_us_posting_is_filtered(normalizer: Normalizer) -> None:
    job = normalizer.normalize(raw_job(location="Seattle, WA"), now=NOW)
    assert job.country == "US"
    assert (job.status, job.filter_reason) == (JobStatus.FILTERED_OUT, "not_canada")


def test_unknown_location_is_kept(normalizer: Normalizer) -> None:
    job = normalizer.normalize(raw_job(location=None), now=NOW)
    assert job.country is None
    assert job.status is JobStatus.NEW


def test_stale_posting_is_filtered(normalizer: Normalizer) -> None:
    job = normalizer.normalize(raw_job(posted_at=datetime(2026, 8, 1, tzinfo=UTC)), now=NOW)
    assert (job.status, job.filter_reason) == (JobStatus.FILTERED_OUT, "stale")


def test_other_language_is_filtered(normalizer: Normalizer) -> None:
    job = normalizer.normalize(
        raw_job(
            title="Desarrollador de software",
            location=None,
            description=posting("es_desarrollador.txt"),
        ),
        now=NOW,
    )
    assert job.language is None
    assert (job.status, job.filter_reason) == (JobStatus.FILTERED_OUT, "unsupported_language")


def test_linkedin_stub_needs_details(normalizer: Normalizer) -> None:
    job = normalizer.normalize(
        raw_job(
            source="manual_linkedin",
            source_id="li-3920011",
            title="Project Manager, Digital Transformation (Contract)",
            company="Example Telecom Corp.",
            location="Ottawa, ON (Remote)",
            description=None,
        ),
        now=NOW,
    )
    assert (job.status, job.filter_reason) == (JobStatus.NEEDS_DETAILS, None)
    assert job.employment_type is EmploymentType.CONTRACT
    assert job.skills == ["project management"]
    assert job.description_raw is None
    assert job.remote_type is RemoteType.REMOTE


def test_structured_permanent_overrides_text(normalizer: Normalizer) -> None:
    job = normalizer.normalize(raw_job(employment_type="permanent"), now=NOW)
    assert job.filter_reason == "not_contract"


def test_normalization_is_deterministic_and_csv_safe(normalizer: Normalizer, store) -> None:
    first = normalizer.normalize(raw_job(), now=NOW)
    assert normalizer.normalize(raw_job(), now=NOW) == first
    store.jobs.append([first])
    assert store.jobs.read_all() == [first]


def test_job_model_accepts_output_types(normalizer: Normalizer) -> None:
    job = normalizer.normalize(raw_job(), now=NOW)
    assert Job.model_validate(job.model_dump()) == job
