from datetime import UTC, datetime

import pytest

from jobmatch.sources.paste import PasteFormatError, canonical_url, parse_paste

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)

FULL = """# comment lines are ignored
url: https://www.linkedin.com/jobs/view/senior-analyst-at-example-4012345678/?trk=abc
title: Senior Business Analyst (12-month contract)
company: Example Insurance Inc.
location: Toronto, ON (Hybrid)
employment_type:
posted: 2026-10-18
---
About the job
12-month contract. Requirements: SQL, BPMN.
"""


def parse(text: str):
    return parse_paste(text, file_name="job.txt", fetched_at=NOW)


def test_full_english_paste() -> None:
    raw, record = parse(FULL)
    assert (raw.source, raw.source_id) == ("manual_linkedin", "4012345678")
    assert raw.url == "https://www.linkedin.com/jobs/view/4012345678/"
    assert raw.title == "Senior Business Analyst (12-month contract)"
    assert raw.employment_type is None
    assert raw.posted_at == datetime(2026, 10, 18, tzinfo=UTC)
    assert raw.description is not None and raw.description.startswith("About the job")
    assert record.payload["file"] == "job.txt"
    assert record.content_hash == parse(FULL)[1].content_hash  # stable


def test_french_paste_with_bom_and_crlf() -> None:
    text = (
        "﻿url: https://emplois.exemple.ca/postes/123?utm_source=linkedin\r\n"
        "titre_inconnu_ignoré: non\r\n"
    )
    with pytest.raises(PasteFormatError, match="line 2: unknown field"):
        parse(text + "---\r\nTexte\r\n")
    raw, _ = parse(
        "﻿url: https://emplois.exemple.ca/postes/123?utm_source=linkedin\r\n"
        "title: Analyste d'affaires – contrat de 6 mois\r\n"
        "location: Montréal (QC)\r\n---\r\nMandat de 6 mois en mode hybride.\r\n"
    )
    assert raw.source == "manual"
    assert raw.url == "https://emplois.exemple.ca/postes/123"  # tracking parameter dropped
    assert raw.description == "Mandat de 6 mois en mode hybride."


def test_header_only_is_a_stub() -> None:
    raw, _ = parse(
        "url: https://www.linkedin.com/jobs/search/?currentJobId=4019876543\n"
        "title: Gestionnaire de projet TI (mandat)\n---\n"
    )
    assert (raw.source, raw.source_id) == ("manual_linkedin", "4019876543")
    assert raw.description is None


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("url: https://x.ca/1\ntitle: A\nNo separator here\n", "missing the '---' line"),
        ("title: Analyst\n---\ntext", "'url' field is required"),
        ("url: https://x.ca/1\n---\ntext", "'title' field is required"),
        ("url: x.ca/1\ntitle: A\n---\n", "must start with http"),
        ("url: https://x.ca/1\ntitle: A\nposted: last week\n---\n", "posted must be a date"),
        (
            "url: https://x.ca/1\ntitle: A\njust some words\n---\n",
            "line 3: expected 'field: value'",
        ),
    ],
)
def test_bad_files_explain_the_problem(text: str, message: str) -> None:
    with pytest.raises(PasteFormatError, match=message):
        parse(text)


def test_same_page_with_different_tracking_gets_same_id() -> None:
    a = canonical_url("https://Jobs.Example.ca/postes/123/?utm_campaign=x#apply")
    b = canonical_url("https://jobs.example.ca/postes/123")
    assert a == b
    assert a[1].startswith("url-")
