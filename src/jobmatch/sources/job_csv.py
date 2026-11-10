"""Jobs entered by a reviewer: a jobs CSV file, or the app's "Add a job by hand" form, which
saves the same format (user decisions 2026-11-10).

    url, job_title, company, location, work_mode, hours_per_week, pay, pay_type,
    posted_date, responsibility, qualification

One row per posting, after a header row. A human typed or pasted these details, so they are
stored as given, without the normaliser (no filters, no title or pay parsing): each cell is
only cleaned of what copying from a web page or a document drags along (HTML tags, bullet
and numbering marks, bold markers, odd spaces, empty lines). `responsibility` and
`qualification` hold several items, one per line inside the cell (Alt+Enter in Excel) or
separated by "|". `work_mode` is on-site, hybrid or remote; `hours_per_week` a number; `pay`
a number with `pay_type` salary (per year) or hourly (French words are accepted too).

Rules (CLAUDE.md CSV rules): read with utf-8-sig; the header is checked, and every problem
names the file and the row; a bad row is skipped and reported, the other rows go in. `url`
and `job_title` are required: the link is how the same job is recognised again. Dates are
YYYY-MM-DD (or with a month name, "8 Nov 2026", "8 novembre 2026"); an all-number date such
as 03/04/2026 is refused, since its day and month cannot be told apart. Cell text is
untrusted posting text: it is never executed, and llm/payload.py neutralises it before
Gemini sees it.
"""

import csv
import html
import io
import re
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import BaseModel, field_validator, model_validator

from jobmatch.normalize.contact import contact_email
from jobmatch.normalize.pipeline import job_id_for
from jobmatch.schemas import Job, JobStatus, PayUnit, RemoteType
from jobmatch.sources.paste import PasteFormatError, canonical_url

COLUMNS = (
    "url", "job_title", "company", "location", "work_mode", "hours_per_week", "pay", "pay_type",
    "posted_date", "responsibility", "qualification",
)  # fmt: skip
REQUIRED = ("url", "job_title")
LIST_COLUMNS = ("responsibility", "qualification")
JOB_CSV_SUFFIX = ".csv"
FORM_MARK = "-form-"  # in the file name of a job saved from the app's form

_INVISIBLE = re.compile("[​-‏  ⁠﻿­]")
_SPACES = re.compile(r"[ \t  -   　]+")
_TAG = re.compile(r"<[^>]{0,500}>")
_EMPHASIS = re.compile(r"(\*\*|__|`)")
_BULLET = re.compile(r"^(?:[-*•·▪◦‣●○■□➢➤►✓✔–—]+|\(?\d{1,2}[.)]|\(?[a-z][.)])\s+", re.I)
_ITEM_SPLIT = re.compile(r"\r\n|\r|\n|\|")

_MONTHS = {
    "jan": 1, "january": 1, "janvier": 1, "janv": 1, "feb": 2, "february": 2, "fevrier": 2,
    "févr": 2, "février": 2, "fev": 2, "mar": 3, "march": 3, "mars": 3, "apr": 4, "april": 4,
    "avril": 4, "avr": 4, "may": 5, "mai": 5, "jun": 6, "june": 6, "juin": 6, "jul": 7,
    "july": 7, "juillet": 7, "juil": 7, "aug": 8, "august": 8, "aout": 8, "août": 8,
    "sep": 9, "sept": 9, "september": 9, "septembre": 9, "oct": 10, "october": 10,
    "octobre": 10, "nov": 11, "november": 11, "novembre": 11, "dec": 12, "december": 12,
    "decembre": 12, "déc": 12, "décembre": 12,
}  # fmt: skip
_DAY_MONTH_YEAR = re.compile(r"^(\d{1,2})(?:er)?\s+([^\W\d_]+)\.?,?\s+(\d{4})$")
_MONTH_DAY_YEAR = re.compile(r"^([^\W\d_]+)\.?\s+(\d{1,2}),?\s+(\d{4})$")


# Accepted words (English and French) -> the value written back to the file.
WORK_MODES = {
    "on-site": "on-site", "onsite": "on-site", "on site": "on-site", "in office": "on-site",
    "sur place": "on-site", "présentiel": "on-site", "presentiel": "on-site",
    "en personne": "on-site", "hybrid": "hybrid", "hybride": "hybrid", "remote": "remote",
    "à distance": "remote", "a distance": "remote", "télétravail": "remote",
    "teletravail": "remote",
}  # fmt: skip
WORK_MODE_TYPES = {
    "on-site": RemoteType.ONSITE, "hybrid": RemoteType.HYBRID, "remote": RemoteType.REMOTE,
}  # fmt: skip
PAY_TYPES = {
    "salary": "salary", "salaried": "salary", "annual": "salary", "yearly": "salary",
    "salaire": "salary", "annuel": "salary", "hourly": "hourly", "hour": "hourly",
    "horaire": "hourly", "à l'heure": "hourly", "a l'heure": "hourly",
}  # fmt: skip
PAY_UNITS = {"salary": PayUnit.YEAR, "hourly": PayUnit.HOUR}
CURRENCY = "CAD"  # Canada only (CLAUDE.md)
MAX_HOURS_PER_WEEK = 80


def parse_number(text: str) -> float:
    """A number as people type it: "85", "37.5", "37,5", "$85,000", "90 000 $"."""
    value = re.sub(r"[\s$]|CAD|\$", "", text, flags=re.I)
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", value):
        value = value.replace(",", "")  # thousands separators
    value = value.replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", value):
        raise ValueError(f"{text!r} is not a number")
    return float(value)


class JobCsvError(Exception):
    """A whole file cannot be read (wrong header, not UTF-8). Names the file."""


def clean_line(value: str) -> str:
    """One line as typed or pasted, without formatting: HTML tags and entities, bold
    markers, invisible characters and odd spaces removed, whitespace collapsed."""
    text = unicodedata.normalize("NFKC", html.unescape(value))
    text = _INVISIBLE.sub("", _TAG.sub(" ", text))
    text = _EMPHASIS.sub("", text)
    return _SPACES.sub(" ", text).strip()


def clean_cell(value: str | None) -> str:
    """A one-line field: line breaks become spaces."""
    return clean_line(re.sub(r"[\r\n]+", " ", value or ""))


def clean_items(value: str | None) -> list[str]:
    """A list field: one item per line (or per "|"), bullet and numbering marks removed,
    empty lines dropped."""
    items = []
    for part in _ITEM_SPLIT.split(value or ""):
        line = _BULLET.sub("", clean_line(part)).strip()
        if line:
            items.append(line)
    return items


def parse_posted(value: str) -> datetime | None:
    """YYYY-MM-DD (or YYYY/MM/DD), or a date with a month name in English or French."""
    text = clean_cell(value)
    if not text:
        return None
    day: date | None = None
    iso = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T].*)?", text)
    try:
        if iso:
            day = date(int(iso[1]), int(iso[2]), int(iso[3]))
        elif m := _DAY_MONTH_YEAR.match(text):
            day = date(int(m[3]), _month(m[2]), int(m[1]))
        elif m := _MONTH_DAY_YEAR.match(text):
            day = date(int(m[3]), _month(m[1]), int(m[2]))
    except (ValueError, KeyError):
        day = None
    if day is None:
        raise ValueError(
            f"posted_date {text!r}: use YYYY-MM-DD (e.g. 2026-11-08) or a month name "
            "(8 Nov 2026); a date written only with numbers is ambiguous"
        )
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _month(name: str) -> int:
    return _MONTHS[name.casefold().rstrip(".")]


class JobEntry(BaseModel):
    """One posting as the reviewer entered it, cleaned."""

    url: str
    job_title: str
    company: str = ""
    location: str = ""
    work_mode: str = ""  # "", on-site, hybrid or remote
    hours_per_week: str = ""  # a number, as text
    pay: str = ""  # a number, as text
    pay_type: str = ""  # "", salary or hourly
    posted_date: str = ""
    responsibility: list[str] = []
    qualification: list[str] = []

    @field_validator("url", "job_title", mode="after")
    @classmethod
    def _required(cls, value: str) -> str:
        if not value:
            raise ValueError("is empty")
        return value

    @field_validator("work_mode", mode="after")
    @classmethod
    def _work_mode(cls, value: str) -> str:
        if value and value.casefold() not in WORK_MODES:
            raise ValueError(f"{value!r}: use on-site, hybrid or remote")
        return WORK_MODES.get(value.casefold(), "")

    @field_validator("hours_per_week", mode="after")
    @classmethod
    def _hours(cls, value: str) -> str:
        if not value:
            return ""
        hours = parse_number(
            re.sub(r"\s*(h|hrs?|hours?|heures?)(\s*/\s*\w+)?$", "", value, flags=re.I)
        )
        if not 0 < hours <= MAX_HOURS_PER_WEEK:
            raise ValueError(f"{value!r}: hours per week, between 1 and {MAX_HOURS_PER_WEEK}")
        return f"{hours:g}"

    @field_validator("pay", mode="after")
    @classmethod
    def _pay(cls, value: str) -> str:
        if not value:
            return ""
        amount = parse_number(value)
        if amount <= 0:
            raise ValueError(f"{value!r}: a pay above 0")
        return f"{amount:g}"

    @field_validator("pay_type", mode="after")
    @classmethod
    def _pay_type(cls, value: str) -> str:
        if value and value.casefold() not in PAY_TYPES:
            raise ValueError(f"{value!r}: use salary or hourly")
        return PAY_TYPES.get(value.casefold(), "")

    @model_validator(mode="after")
    def _pay_needs_type(self) -> "JobEntry":
        if self.pay and not self.pay_type:
            raise ValueError("pay_type: say whether the pay is salary or hourly")
        return self

    @classmethod
    def from_cells(cls, cells: dict[str, str | None]) -> "JobEntry":
        data: dict[str, object] = {}
        for column in COLUMNS:
            raw = cells.get(column)
            data[column] = clean_items(raw) if column in LIST_COLUMNS else clean_cell(raw)
        return cls.model_validate(data)

    def to_cells(self) -> list[str]:
        return [
            "\n".join(getattr(self, c)) if c in LIST_COLUMNS else getattr(self, c) for c in COLUMNS
        ]

    def to_job(self, now: datetime) -> Job:
        """The stored job: the entry as given (status new, no filters, no parsing)."""
        try:
            source, source_id, url = canonical_url(self.url)
        except PasteFormatError as exc:
            raise ValueError(str(exc)) from exc
        posted = parse_posted(self.posted_date)
        hours = float(self.hours_per_week) if self.hours_per_week else None
        pay = float(self.pay) if self.pay else None
        unit = PAY_UNITS[self.pay_type] if pay is not None else None
        lines = [
            self.job_title,
            *(f"- {r}" for r in self.responsibility),
            *(f"- {q}" for q in self.qualification),
        ]
        text = "\n".join(lines)
        return Job(
            job_id=job_id_for(source, source_id),
            source=source,
            source_id=source_id,
            url=url,
            title_raw=self.job_title,
            company_raw=self.company or None,
            location_raw=self.location or None,
            responsibilities=self.responsibility,
            requirements=self.qualification,
            description_raw=text,
            description_clean=text,
            remote_type=WORK_MODE_TYPES.get(self.work_mode, RemoteType.UNKNOWN),
            hours_per_week=hours,
            pay_min=pay,
            pay_max=pay,
            pay_unit=unit,
            pay_currency=CURRENCY if pay is not None else None,
            posted_at=posted,
            first_seen_at=now,
            status=JobStatus.NEW,
            contact_email=contact_email(text),
        )


class RowResult(BaseModel):
    row: int  # the line number in the file (the header is row 1)
    job: Job | None = None
    error: str | None = None


def read_job_csv(path: Path, now: datetime) -> list[RowResult]:
    """Every row of a jobs CSV file, as a job or as the reason it was refused."""
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise JobCsvError(
            f"{path.name}: not UTF-8 text. In Excel, save it as 'CSV UTF-8 (comma delimited)'."
        ) from exc
    lines = text.splitlines(keepends=True)
    if lines and lines[0].strip().lower().startswith("sep="):  # Excel's separator hint
        lines = lines[1:]
    reader = csv.reader(io.StringIO("".join(lines)))
    header = [h.strip().lower() for h in next(reader, [])]
    if not header:
        raise JobCsvError(f"{path.name}: the file is empty (no header row)")
    unknown = [h for h in header if h and h not in COLUMNS]
    missing = [c for c in REQUIRED if c not in header]
    if unknown or missing:
        problems = []
        if missing:
            problems.append(f"missing column(s): {', '.join(missing)}")
        if unknown:
            problems.append(f"unknown column(s): {', '.join(unknown)}")
        raise JobCsvError(
            f"{path.name} row 1: {'; '.join(problems)}. Expected: {', '.join(COLUMNS)}"
        )
    out: list[RowResult] = []
    # Rows are counted as a spreadsheet shows them (a cell with line breaks is one row).
    for row_no, cells in enumerate(reader, start=2):
        if not any(c.strip() for c in cells):
            continue
        if len(cells) > len(header):
            out.append(RowResult(row=row_no, error=f"{len(cells)} cells for {len(header)} columns"))
            continue
        values = dict(zip(header, cells + [""] * (len(header) - len(cells)), strict=True))
        try:
            entry = JobEntry.from_cells(values)
            out.append(RowResult(row=row_no, job=entry.to_job(now)))
        except ValueError as exc:
            out.append(RowResult(row=row_no, error=_reason(exc)))
    return out


def _reason(exc: ValueError) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg'].removeprefix('Value error, ')}"
            for e in errors(include_input=False, include_url=False)
        )
    return str(exc)


def write_job_csv(path: Path, entries: list[JobEntry]) -> None:
    """A jobs CSV in the intake format (UTF-8 with BOM, so Excel opens it correctly)."""
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(COLUMNS)
        writer.writerows(e.to_cells() for e in entries)


TEMPLATE_ROWS = [
    JobEntry(
        url="https://www.example.ca/jobs/12345",
        job_title="Data Analyst (6-month contract)",
        company="Example Insurance Inc.",
        location="Toronto, ON",
        work_mode="hybrid",
        hours_per_week="40",
        pay="90",
        pay_type="hourly",
        posted_date="2026-11-08",
        responsibility=["Build Power BI dashboards", "Write SQL against the data warehouse"],
        qualification=["3+ years of SQL", "Power BI", "French is an asset"],
    ),
    JobEntry(
        url="https://www.exemple.qc.ca/emplois/678",
        job_title="Analyste d'affaires (mandat de 12 mois)",
        company="Ville de Québec",
        location="Québec, QC",
        work_mode="on-site",
        hours_per_week="35",
        pay="95000",
        pay_type="salary",
        posted_date="2026-11-05",
        responsibility=["Documenter les processus", "Animer les ateliers avec les équipes"],
        qualification=["5 ans en analyse d'affaires", "Français et anglais"],
    ),
]
