"""Turn a fetched posting page into a RawJob, the same input the manual paste path gives the
normalizer.

Most job boards and ATS pages embed a schema.org JobPosting block (JSON-LD). When there is
one, its title, company, location, employment type, salary, date and description are used.
Otherwise the page title (og:title or <title>) and its main content (without navigation,
header, footer and scripts) are used. Nothing in the page is executed.
"""

import html as html_lib
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

from bs4 import BeautifulSoup
from pydantic import BaseModel

from jobmatch.schemas import PayUnit, RawJob

MAX_MAIN_HTML_CHARS = 200_000
_NOISE_TAGS = [
    "script", "style", "noscript", "template", "nav", "header", "footer", "aside", "form",
    "svg", "iframe", "button",
]  # fmt: skip
_COUNTRY_NAMES = {"CA": "Canada", "US": "United States"}
_PAY_UNITS = {"HOUR": PayUnit.HOUR, "DAY": PayUnit.DAY, "WEEK": PayUnit.WEEK,
              "MONTH": PayUnit.MONTH, "YEAR": PayUnit.YEAR}  # fmt: skip


class PageData(BaseModel):
    page_title: str | None
    job_posting: dict[str, Any] | None
    main_html: str | None  # only kept when there is no JobPosting block


class PageError(ValueError):
    """A page with nothing usable as a job posting."""


def extract_page(page_html: str) -> PageData:
    soup = BeautifulSoup(page_html, "lxml")
    title = _page_title(soup)
    posting = _find_job_posting(soup)
    if posting is not None:
        return PageData(page_title=title, job_posting=posting, main_html=None)
    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"})
    main = main or soup.body
    main_html = str(main)[:MAX_MAIN_HTML_CHARS] if main else None
    return PageData(page_title=title, job_posting=None, main_html=main_html)


def raw_job_from_page(
    page: PageData, *, source: str, source_id: str, url: str, fetched_at: datetime
) -> RawJob:
    posting = page.job_posting or {}
    title = _text(posting.get("title")) or page.page_title
    if not title:
        raise PageError("no job title found on the page")
    pay = _pay(posting.get("baseSalary"))
    return RawJob(
        source=source,
        source_id=source_id,
        url=url,
        title=title,
        company=_organization(posting.get("hiringOrganization")),
        location=_location(posting) if posting else None,
        description=_description(posting) if posting else page.main_html,
        employment_type=_employment_type(posting.get("employmentType")),
        pay_min=pay[0],
        pay_max=pay[1],
        pay_unit=pay[2],
        pay_currency=pay[3],
        posted_at=_date(posting.get("datePosted")),
        fetched_at=fetched_at,
    )


# ---- page structure ----


def _page_title(soup: BeautifulSoup) -> str | None:
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        return str(og["content"]).strip() or None
    if soup.title and soup.title.string:
        return soup.title.string.strip() or None
    return None


def _find_job_posting(soup: BeautifulSoup) -> dict[str, Any] | None:
    for tag in soup.find_all("script", type=lambda t: bool(t) and "ld+json" in t.casefold()):
        raw = tag.string or tag.get_text()
        try:
            data = json.loads(raw, strict=False)
        except ValueError:
            continue  # broken JSON-LD on some sites: try the next block
        for item in _walk(data):
            types = item.get("@type")
            if types == "JobPosting" or (isinstance(types, list) and "JobPosting" in types):
                return item
    return None


def _walk(data: Any) -> Iterator[dict[str, Any]]:
    if isinstance(data, list):
        for element in data:
            yield from _walk(element)
    elif isinstance(data, dict):
        yield data
        if "@graph" in data:
            yield from _walk(data["@graph"])


# ---- JobPosting fields ----


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        cleaned = html_lib.unescape(value).strip()
        return cleaned or None
    return None


def _organization(value: Any) -> str | None:
    if isinstance(value, dict):
        return _text(value.get("name"))
    return _text(value)


def _address_text(address: Any) -> str | None:
    if isinstance(address, str):
        return _text(address)
    if not isinstance(address, dict):
        return None
    country = address.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name")
    if isinstance(country, str):
        country = _COUNTRY_NAMES.get(country.strip().upper(), country)
    parts = [address.get("addressLocality"), address.get("addressRegion"), country]
    joined = ", ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
    return joined or None


def _location(posting: dict[str, Any]) -> str | None:
    places = posting.get("jobLocation")
    places = places if isinstance(places, list) else [places]
    texts = [
        t
        for place in places
        if isinstance(place, dict)
        and (t := _address_text(place.get("address")) or _text(place.get("name")))
    ]
    location = " / ".join(dict.fromkeys(texts))
    location_type = posting.get("jobLocationType")
    if isinstance(location_type, str) and location_type.upper() == "TELECOMMUTE":
        location = f"{location} (Remote)" if location else "Remote"
        if not texts:
            countries = posting.get("applicantLocationRequirements")
            countries = countries if isinstance(countries, list) else [countries]
            names = [
                _COUNTRY_NAMES.get(str(c.get("name", "")).upper(), c.get("name"))
                for c in countries
                if isinstance(c, dict) and c.get("name")
            ]
            if names:
                location = f"Remote - {' / '.join(names)}"
    return location or None


def _description(posting: dict[str, Any]) -> str | None:
    description = posting.get("description")
    if not isinstance(description, str):
        return None
    # Some sites escape the HTML inside JSON-LD ("&lt;p&gt;").
    return html_lib.unescape(description) if "&lt;" in description else description


def _employment_type(value: Any) -> str | None:
    values = value if isinstance(value, list) else [value]
    words = [v.replace("_", " ").strip() for v in values if isinstance(v, str) and v.strip()]
    return ", ".join(words) or None  # "FULL TIME, CONTRACTOR"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _pay(salary: Any) -> tuple[float | None, float | None, PayUnit | None, str | None]:
    if not isinstance(salary, dict):
        return None, None, None, None
    currency = salary.get("currency")
    currency = currency.strip().upper() if isinstance(currency, str) else None
    if currency is not None and (len(currency) != 3 or not currency.isalpha()):
        currency = None
    value = salary.get("value")
    if isinstance(value, dict):
        low = _number(value.get("minValue")) or _number(value.get("value"))
        high = _number(value.get("maxValue")) or _number(value.get("value"))
        unit = value.get("unitText")
    else:
        low = high = _number(value)
        unit = salary.get("unitText")
    pay_unit = _PAY_UNITS.get(unit.strip().upper()) if isinstance(unit, str) else None
    if low is None and high is None:
        return None, None, None, None
    if low is not None and high is not None and low > high:
        low, high = high, low
    return low, high, pay_unit, currency


def _date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
            return datetime(day.year, day.month, day.day, tzinfo=UTC)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
