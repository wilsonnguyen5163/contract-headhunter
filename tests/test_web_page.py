from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobmatch.schemas import PayUnit
from jobmatch.sources.web_page import PageData, PageError, extract_page, raw_job_from_page

PAGES = Path(__file__).resolve().parent / "fixtures" / "pages"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def page(name: str) -> str:
    return (PAGES / name).read_text(encoding="utf-8")


def build(data: PageData):
    return raw_job_from_page(
        data, source="manual", source_id="url-1", url="https://example.org/1", fetched_at=NOW
    )


def test_jobposting_block_is_used() -> None:
    data = extract_page(page("jobposting_ftc.html"))
    assert data.job_posting is not None and data.main_html is None
    raw = build(data)
    assert raw.title == "Senior Campaign Producer - 6 month FTC"
    assert raw.company == "Example Agency"
    assert raw.location == "Vancouver, British Columbia, Canada (Remote)"
    assert raw.employment_type == "FULL TIME"
    assert (raw.pay_min, raw.pay_max, raw.pay_unit, raw.pay_currency) == (
        70000,
        80000,
        PayUnit.YEAR,
        "CAD",
    )
    assert raw.posted_at == datetime(2026, 10, 13, 20, 58, 17, tzinfo=UTC)
    assert raw.description is not None and "<li>" in raw.description


def test_we_offer_section_is_benefits_not_requirements(normalizer) -> None:
    job = normalizer.normalize(build(extract_page(page("jobposting_ftc.html"))), now=NOW)
    assert job.requirements == [
        "5+ years of experience in digital project management",
        "Proficiency in Google Sheets and Slides",
    ]
    assert "hybrid working policy" not in (job.description_clean or "")
    assert job.remote_type.value == "remote"  # the benefits line "hybrid working" is not read


def test_page_without_jobposting_falls_back_to_main_text() -> None:
    data = extract_page(page("no_jsonld.html"))  # broken JSON-LD is ignored, not fatal
    assert data.job_posting is None
    raw = build(data)
    assert raw.title == "Analyste d'affaires – contrat de 12 mois | Exemple Emplois"
    assert raw.description is not None
    assert "Mandat de 12 mois" in raw.description
    for noise in ("Connexion", "Accueil", "Offres similaires", "Politique", "tracking"):
        assert noise not in raw.description


@pytest.mark.parametrize(
    ("snippet", "location"),
    [
        ('"jobLocationType":"TELECOMMUTE","applicantLocationRequirements":{"name":"CA"}',
         "Remote - Canada"),
        ('"jobLocation":[{"address":{"addressLocality":"Toronto","addressRegion":"ON",'
         '"addressCountry":"CA"}},{"address":{"addressLocality":"Ottawa","addressCountry":"CA"}}]',
         "Toronto, ON, Canada / Ottawa, Canada"),
        ('"jobLocation":{"address":"Montréal (QC)"}', "Montréal (QC)"),
    ],
)  # fmt: skip
def test_location_shapes(snippet: str, location: str) -> None:
    html = (
        '<script type="application/ld+json">'
        f'{{"@type":"JobPosting","title":"Analyst",{snippet}}}</script>'
    )
    assert build(extract_page(html)).location == location


def test_hourly_salary_and_contractor_type() -> None:
    html = (
        '<script type="application/ld+json">[{"@type":["JobPosting"],"title":"Developer",'
        '"employmentType":["CONTRACTOR","PART_TIME"],'
        '"baseSalary":{"currency":"cad","value":{"value":95,"unitText":"HOUR"}}}]</script>'
    )
    raw = build(extract_page(html))
    assert raw.employment_type == "CONTRACTOR, PART TIME"
    assert (raw.pay_min, raw.pay_max, raw.pay_unit, raw.pay_currency) == (
        95,
        95,
        PayUnit.HOUR,
        "CAD",
    )


def test_escaped_description_html_is_unescaped() -> None:
    html = (
        '<script type="application/ld+json">{"@type":"JobPosting","title":"Analyst",'
        '"description":"&lt;ul&gt;&lt;li&gt;SQL&lt;/li&gt;&lt;/ul&gt;"}</script>'
    )
    assert build(extract_page(html)).description == "<ul><li>SQL</li></ul>"


def test_page_with_no_title_is_an_error() -> None:
    with pytest.raises(PageError, match="no job title"):
        build(extract_page("<html><body><p>Nothing here</p></body></html>"))
