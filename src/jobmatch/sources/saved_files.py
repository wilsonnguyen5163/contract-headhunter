"""Postings saved from the browser into data/inbox/manual/, for pages that cannot be fetched
(sites that refuse automated requests, pages built by JavaScript such as Workday, LinkedIn).

- .html / .htm ("Save page as"): read like a fetched link. The browser saves the page after
  its JavaScript has run, so a schema.org JobPosting block is used when there is one, else
  the page's main text. The posting URL comes from the canonical link, og:url, or the
  "saved from url" comment the browser writes.
- .pdf ("Print > Save as PDF"): plain text only. The posting URL comes from the browser's
  print footer (on by default), the title from the PDF's document title. Scanned (image)
  PDFs have no text and are reported as errors.

Either way the URL gives the same job id as the link would, so a posting imported both ways
is stored once. Nothing in the file is executed.
"""

import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from jobmatch.schemas import RawJob, RawRecord
from jobmatch.sources.paste import PasteFormatError, canonical_url
from jobmatch.sources.web import decode_page
from jobmatch.sources.web_page import PageError, extract_page, raw_job_from_page

HTML_SUFFIXES = (".html", ".htm")
PDF_SUFFIX = ".pdf"
SAVED_SUFFIXES = (*HTML_SUFFIXES, PDF_SUFFIX)
SAVED_HTML_KIND = "saved_html"
SAVED_PDF_KIND = "saved_pdf"

_SAVED_FROM = re.compile(r"<!--\s*saved from url=\(\d+\)(\S+?)\s*-->", re.IGNORECASE)
_LINKEDIN_JOB = re.compile(r"(?:/jobs/view/|currentJobId=)(\d{6,})")
_URL = re.compile(r"https?://[^\s<>\"']+")
_PAGE_NUMBER = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")
_ELLIPSIS = ("…", "...")


class SavedFileError(PasteFormatError):
    """A saved file that cannot be read as a posting; the message says what to do."""


def read_saved_file(path: Path, *, fetched_at: datetime) -> tuple[RawJob, RawRecord]:
    if path.suffix.lower() == PDF_SUFFIX:
        return _read_pdf(path, fetched_at)
    return _read_html(path, fetched_at)


# ---- saved web pages ----


def saved_page_url(page_html: str) -> str | None:
    """The posting URL of a saved page: canonical link, then og:url, then the browser's
    "saved from url" comment, then (LinkedIn, signed in) the posting number in its links."""
    soup = BeautifulSoup(page_html[:300_000], "lxml")
    canonical = soup.find("link", rel=lambda r: bool(r) and "canonical" in r)
    if canonical and str(canonical.get("href", "")).startswith("http"):
        return str(canonical["href"]).strip()
    og = soup.find("meta", attrs={"property": "og:url"})
    if og and str(og.get("content", "")).startswith("http"):
        return str(og["content"]).strip()
    saved = _SAVED_FROM.search(page_html[:2000])
    if saved:
        return saved.group(1)
    return _linkedin_posting(page_html)


def _linkedin_posting(page_html: str) -> str | None:
    """A LinkedIn page saved while signed in has no canonical link, but the posting number
    appears in its links; the most frequent one is the posting shown."""
    if "linkedin.com" not in page_html:
        return None
    numbers = Counter(_LINKEDIN_JOB.findall(page_html))
    if not numbers:
        return None
    return f"https://www.linkedin.com/jobs/view/{numbers.most_common(1)[0][0]}/"


def _read_html(path: Path, fetched_at: datetime) -> tuple[RawJob, RawRecord]:
    page_html = decode_page(path.read_bytes(), None)
    found = saved_page_url(page_html)
    if not found:
        raise SavedFileError(
            "no posting URL found in the saved page; save it again from the posting, or use a "
            "paste file"
        )
    source, source_id, url = canonical_url(found)
    page = extract_page(page_html)
    try:
        raw_job = raw_job_from_page(
            page, source=source, source_id=source_id, url=url, fetched_at=fetched_at
        )
    except PageError as exc:
        raise SavedFileError(str(exc)) from None
    payload = {"kind": SAVED_HTML_KIND, "file": path.name, "url": url, "page": page.model_dump()}
    return raw_job, _record(source, source_id, fetched_at, payload)


# ---- PDFs printed from the browser ----


def _read_pdf(path: Path, fetched_at: datetime) -> tuple[RawJob, RawRecord]:
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise SavedFileError("the PDF is password-protected; save it again without one")
        pages = [page.extract_text() or "" for page in reader.pages]
        doc_title = (reader.metadata.title if reader.metadata else None) or None
    except PdfReadError as exc:
        raise SavedFileError(f"not a readable PDF ({exc})") from None
    text = "\n".join(pages)
    if not text.strip():
        raise SavedFileError("the PDF has no text (a scan or image); use a paste file instead")
    found = pdf_posting_url(pages)
    if not found:
        raise SavedFileError(
            "no posting URL found in the PDF; print it again with headers and footers on, "
            "save the page as HTML, or use a paste file"
        )
    source, source_id, url = canonical_url(found)
    body = _strip_print_lines(text, url_found=found, title=doc_title)
    title = (doc_title or "").strip() or next(
        (ln.strip() for ln in body.split("\n") if ln.strip()), ""
    )
    try:
        raw_job = RawJob(
            source=source,
            source_id=source_id,
            url=url,
            title=title,
            description=body,
            fetched_at=fetched_at,
        )
    except ValueError as exc:
        raise SavedFileError(f"cannot build a posting from the PDF: {exc}") from None
    payload = {
        "kind": SAVED_PDF_KIND,
        "file": path.name,
        "url": url,
        "title": doc_title,
        "text": body,
    }
    return raw_job, _record(source, source_id, fetched_at, payload)


def pdf_posting_url(pages: list[str]) -> str | None:
    """The URL the browser prints in each page's footer: the one found on the most pages
    (links inside the posting usually appear once). Shortened URLs ending in "…" are not
    usable."""
    counts: Counter[str] = Counter()
    order: dict[str, int] = {}
    for page in pages:
        for url in dict.fromkeys(_URL.findall(page)):
            url = url.rstrip(".,;)")
            if url.endswith(_ELLIPSIS):
                continue
            counts[url] += 1
            order.setdefault(url, len(order))
    if not counts:
        return None
    return max(counts, key=lambda u: (counts[u], -order[u]))


def _strip_print_lines(text: str, *, url_found: str, title: str | None) -> str:
    """Drop the browser's print header and footer: the URL line, "1/3" page numbers, and
    header lines that repeat the document title."""
    kept: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if url_found in stripped and len(stripped) <= len(url_found) + 25:
            continue
        if _PAGE_NUMBER.match(stripped):
            continue
        if title and stripped.endswith(title.strip()) and len(stripped) <= len(title) + 25:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _record(source: str, source_id: str, now: datetime, payload: dict[str, Any]) -> RawRecord:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return RawRecord(
        source=source, source_id=source_id, fetched_at=now, content_hash=digest, payload=payload
    )
