"""Postings saved from the browser as .html or .pdf (sources/saved_files.py). All content
is synthetic; PDFs are built byte by byte, so no PDF writer is needed."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobmatch.ingest import ingest_paste_files
from jobmatch.normalize.pipeline import Normalizer
from jobmatch.sources.paste import canonical_url
from jobmatch.sources.saved_files import (
    SavedFileError,
    pdf_posting_url,
    read_saved_file,
    saved_page_url,
)
from jobmatch.storage.csv_store import CsvJobRepository, CsvStore

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
URL = "https://jobs.example.org/example-co/jobs/content-producer-12-month-contract-1a2b3c"


def make_pdf(path: Path, pages: list[list[str]], title: str | None = None) -> Path:
    """A minimal text PDF: one Helvetica text block per page, one line per string."""

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b""]
    objects[2] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    kids = []
    for lines in pages:
        body = (
            "BT /F1 10 Tf 14 TL 50 760 Td " + " ".join(f"({esc(ln)}) Tj T*" for ln in lines) + " ET"
        )
        stream = body.encode("latin-1")
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        content_ref = len(objects)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % content_ref
        )
        kids.append(len(objects))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        " ".join(f"{k} 0 R" for k in kids).encode(),
        len(kids),
    )
    info_ref = None
    if title is not None:
        objects.append(b"<< /Title (%s) >>" % esc(title).encode("latin-1"))
        info_ref = len(objects)
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    trailer = b"<< /Size %d /Root 1 0 R" % (len(objects) + 1)
    if info_ref:
        trailer += b" /Info %d 0 R" % info_ref
    out += b"trailer\n" + trailer + b" >>\nstartxref\n%d\n%%%%EOF\n" % xref
    path.write_bytes(bytes(out))
    return path


POSTING_LINES = [
    "Content Producer (12-Month Contract)",
    "Example Co - Vancouver, BC - Remote",
    "Responsibilities",
    "- Produce short-form video for TikTok, Instagram and YouTube",
    "- Manage the editorial calendar with the social team",
    "Requirements",
    "- 3+ years of content production experience",
    "Learn more about us at https://example.org/about",
]


def test_pdf_footer_url_header_and_page_numbers(tmp_path: Path) -> None:
    title = "Content Producer (12-Month Contract) | Example Co"
    pdf = make_pdf(
        tmp_path / "posting.pdf",
        [
            [f"10/26/26, 7:43 PM {title}", *POSTING_LINES[:5], f"{URL} 1/2"],
            [f"10/26/26, 7:43 PM {title}", *POSTING_LINES[5:], f"{URL} 2/2"],
        ],
        title=title,
    )
    raw, record = read_saved_file(pdf, fetched_at=NOW)
    assert (raw.url, raw.source_id) == (canonical_url(URL)[2], canonical_url(URL)[1])
    assert raw.title == title
    assert "Manage the editorial calendar" in (raw.description or "")
    for printed in ("7:43 PM", "1/2", URL):
        assert printed not in (raw.description or "")
    assert "https://example.org/about" in (raw.description or "")  # links in the text stay
    assert record.payload["kind"] == "saved_pdf"


def test_pdf_footer_url_wins_over_a_link_in_the_text() -> None:
    pages = [f"intro https://example.org/about\n{URL} 1/2", f"{URL} 2/2"]
    assert pdf_posting_url(pages) == URL
    assert pdf_posting_url(["https://jobs.example.org/example-co/jobs/content-pro…"]) is None


def test_pdf_without_title_uses_its_first_line(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "p.pdf", [[*POSTING_LINES, URL]])
    raw, _ = read_saved_file(pdf, fetched_at=NOW)
    assert raw.title == "Content Producer (12-Month Contract)"


@pytest.mark.parametrize(
    ("pages", "message"),
    [
        ([["Content Producer", "no address printed anywhere"]], "no posting URL found in the PDF"),
        ([[]], "the PDF has no text"),
    ],
)
def test_unusable_pdfs_say_what_to_do(tmp_path: Path, pages: list[list[str]], message: str) -> None:
    with pytest.raises(SavedFileError, match=message):
        read_saved_file(make_pdf(tmp_path / "p.pdf", pages), fetched_at=NOW)


def test_not_a_pdf(tmp_path: Path) -> None:
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"this is not a pdf")
    with pytest.raises(SavedFileError, match="not a readable PDF"):
        read_saved_file(bad, fetched_at=NOW)


# ---- saved web pages ----


def saved_page(body: str, head: str = "", saved_from: str | None = URL) -> str:
    comment = f"<!-- saved from url=({len(saved_from)}){saved_from} -->\n" if saved_from else ""
    return f"<!DOCTYPE html>\n{comment}<html><head>{head}</head><body>{body}</body></html>"


MAIN = """<main><h1>Content Producer (12-Month Contract)</h1>
<h2>Responsibilities</h2><ul><li>Produce short-form video for TikTok and YouTube</li>
<li>Manage the editorial calendar</li></ul>
<h2>Requirements</h2><ul><li>3+ years of content production experience</li></ul></main>
<script>window.__data = {"salaryMin": 1}</script>"""


def test_saved_page_without_structured_data_uses_page_title_and_main_text(tmp_path: Path) -> None:
    head = (
        '<meta property="og:title" '
        'content="Content Producer (12-Month Contract) - Expired | Example Board">'
    )
    page = tmp_path / "Content Producer _ Example.html"
    page.write_text(saved_page(MAIN, head), encoding="utf-8")
    raw, record = read_saved_file(page, fetched_at=NOW)
    assert raw.url == canonical_url(URL)[2]
    assert raw.title == "Content Producer (12-Month Contract) - Expired | Example Board"
    assert "Manage the editorial calendar" in (raw.description or "")
    assert "salaryMin" not in (raw.description or "")  # scripts never become text
    assert record.payload["kind"] == "saved_html"


def test_saved_page_with_a_job_posting_block(tmp_path: Path) -> None:
    ld = (
        '<script type="application/ld+json">{"@context": "https://schema.org", '
        '"@type": "JobPosting", "title": "Rédactrice web (contrat de 12 mois)", '
        '"hiringOrganization": {"@type": "Organization", "name": "Exemple inc."}, '
        '"employmentType": "CONTRACTOR", "description": "<p>Rédiger des contenus.</p>", '
        '"baseSalary": {"@type": "MonetaryAmount", "currency": "CAD", "value": '
        '{"@type": "QuantitativeValue", "minValue": 60000, "maxValue": 70000, "unitText": "YEAR"}}}'
        "</script>"
    )
    head = f'<link rel="canonical" href="{URL}">{ld}'
    page = tmp_path / "offre.htm"
    page.write_text(saved_page("<p>shell</p>", head, saved_from=None), encoding="utf-8")
    raw, _ = read_saved_file(page, fetched_at=NOW)
    assert (raw.title, raw.company, raw.pay_min, raw.pay_max) == (
        "Rédactrice web (contrat de 12 mois)",
        "Exemple inc.",
        60000,
        70000,
    )


def test_saved_page_url_order() -> None:
    other = "https://jobs.example.org/other"
    both = saved_page(
        "", f'<link rel="canonical" href="{URL}"><meta property="og:url" content="{other}">', other
    )
    assert saved_page_url(both) == URL
    assert (
        saved_page_url(saved_page("", f'<meta property="og:url" content="{other}">', None)) == other
    )
    assert saved_page_url(saved_page("", "", URL)) == URL
    assert saved_page_url(saved_page("", "", None)) is None


def test_saved_page_without_url_is_an_error(tmp_path: Path) -> None:
    page = tmp_path / "p.html"
    page.write_text(saved_page(MAIN, saved_from=None), encoding="utf-8")
    with pytest.raises(SavedFileError, match="no posting URL found in the saved page"):
        read_saved_file(page, fetched_at=NOW)


# ---- through manual intake ----


def test_manual_intake_reads_txt_html_and_pdf(normalizer: Normalizer, tmp_path: Path) -> None:
    inbox, data_dir = tmp_path / "manual", tmp_path / "data"
    inbox.mkdir()
    head = "<title>Content Producer (12-Month Contract)</title>"
    (inbox / "saved.html").write_text(saved_page(MAIN, head), encoding="utf-8")
    (inbox / "saved_files").mkdir()  # the browser's assets folder is ignored
    other = "https://jobs.example.org/example-co/jobs/video-editor-contract-9z8y"
    make_pdf(
        inbox / "printed.pdf", [["Video Editor (6-Month Contract)", *POSTING_LINES[2:7], other]]
    )
    (inbox / "_TEMPLATE.txt").write_text("template", encoding="utf-8")
    (inbox / "notes.docx").write_bytes(b"ignored")
    report = ingest_paste_files(
        [inbox], data_dir=data_dir, lock_timeout=5, normalizer=normalizer, now=NOW
    )
    outcomes = {Path(o.file).name: o.outcome for o in report.outcomes}
    assert outcomes == {"saved.html": "new", "printed.pdf": "new", "_TEMPLATE.txt": "skipped"}
    titles = {j.title_raw for j in CsvJobRepository(CsvStore(data_dir, lock_timeout=5)).list_all()}
    assert titles == {"Content Producer (12-Month Contract)", "Video Editor (6-Month Contract)"}

    again = ingest_paste_files(
        [inbox], data_dir=data_dir, lock_timeout=5, normalizer=normalizer, now=NOW
    )
    assert {o.outcome for o in again.outcomes if not o.file.endswith(".txt")} == {"already_stored"}
