"""Pydantic v2 models for every record that crosses a module boundary.

For the CSV-backed records, the CSV columns are the field names in declaration order.
IDs and NOC codes are always strings (Excel strips leading zeros).
"""

from enum import StrEnum
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 3  # 2: responsibilities, nice_to_have, years_experience_min; 3: commitment

_PROVINCES = "AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT"
PROVINCE_CODES = frozenset(_PROVINCES.split("|"))

NonEmptyStr = Annotated[str, Field(min_length=1)]
NocCode = Annotated[str, Field(pattern=r"^\d{5}$")]  # NOC 2021 codes, e.g. "00012"
Province = Annotated[str, Field(pattern=rf"^CA-({_PROVINCES})$")]  # ISO 3166-2:CA
Region = Annotated[str, Field(pattern=rf"^CA(-({_PROVINCES}))?$")]  # "CA" = anywhere in Canada
CountryCode = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
LanguageCode = Annotated[str, Field(pattern=r"^[a-z]{2}$")]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- enums ----


class RemoteType(StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class RemotePreference(StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    ANY = "any"


class EmploymentType(StrEnum):
    CONTRACT = "contract"
    PERMANENT = "permanent"
    TEMPORARY = "temporary"
    UNKNOWN = "unknown"


class Commitment(StrEnum):
    """Hours commitment, independent of employment type: a contract can be full- or part-time."""

    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    UNKNOWN = "unknown"


class Language(StrEnum):
    EN = "en"
    FR = "fr"
    BILINGUAL = "bilingual"


class JobStatus(StrEnum):
    NEW = "new"
    NEEDS_DETAILS = "needs_details"
    FILTERED_OUT = "filtered_out"
    MATCHED = "matched"
    NO_MATCH = "no_match"


class PayUnit(StrEnum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class Decision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_INFO = "needs_info"
    # The reviewer reset their decision (app "Reset"): the match counts as undecided again.
    # reviews.csv is append-only, so a reset is a new row, and the history stays.
    CLEARED = "cleared"


class ReasonCode(StrEnum):
    WRONG_SKILLS = "wrong_skills"
    SENIORITY = "seniority"
    LOCATION = "location"
    LANGUAGE = "language"
    NOT_CONTRACT = "not_contract"
    DUPLICATE = "duplicate"
    EXPIRED = "expired"
    OTHER = "other"


class FilterReason(StrEnum):
    NOT_CANADA = "not_canada"
    NOT_CONTRACT = "not_contract"
    STALE = "stale"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    # Stored as "duplicate:<job_id of the original>".
    DUPLICATE = "duplicate"


class RunStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


# ---- intake ----


class RawRecord(BaseModel):
    """A posting exactly as received, kept append-only in data/raw/<source>-YYYY-MM.jsonl."""

    model_config = ConfigDict(extra="forbid")

    source: Annotated[str, Field(pattern=r"^[a-z0-9_]+$")]
    source_id: NonEmptyStr
    fetched_at: AwareDatetime
    content_hash: NonEmptyStr  # sha256 of the payload, to skip identical re-imports
    payload: dict[str, Any]


class RawJob(Record):
    """A posting as received from a source or manual intake, before normalization."""

    source: NonEmptyStr
    source_id: NonEmptyStr
    url: NonEmptyStr
    title: NonEmptyStr
    company: str | None = None
    location: str | None = None
    description: str | None = None  # plain text or HTML, untrusted
    employment_type: str | None = None  # the source's own structured value, as given
    pay_min: float | None = Field(default=None, ge=0)
    pay_max: float | None = Field(default=None, ge=0)
    pay_unit: PayUnit | None = None
    pay_currency: CurrencyCode | None = None
    noc_code: NocCode | None = None
    posted_at: AwareDatetime | None = None
    fetched_at: AwareDatetime

    @model_validator(mode="after")
    def _pay_range(self) -> "RawJob":
        if self.pay_min is not None and self.pay_max is not None and self.pay_min > self.pay_max:
            raise ValueError("pay_min is greater than pay_max")
        return self


# ---- CSV records ----


class Job(Record):
    """One normalized posting (data/jobs.csv). Vectors live in the vector store, not here."""

    job_id: NonEmptyStr
    source: NonEmptyStr
    source_id: NonEmptyStr
    url: NonEmptyStr
    title_raw: NonEmptyStr
    title_norm: str | None = None
    company_raw: str | None = None
    company_norm: str | None = None
    location_raw: str | None = None
    city: str | None = None
    province: Province | None = None
    country: CountryCode | None = None
    remote_type: RemoteType = RemoteType.UNKNOWN
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    commitment: Commitment = Commitment.UNKNOWN
    contract_signals: list[str] = []
    duration_months: float | None = Field(default=None, ge=0)
    language: Language | None = None
    description_raw: str | None = None
    description_clean: str | None = None  # the job itself: no employer pitch, benefits or pay
    responsibilities: list[str] = []
    requirements: list[str] = []
    nice_to_have: list[str] = []
    skills: list[str] = []
    seniority: str | None = None
    years_experience_min: float | None = Field(default=None, ge=0)
    pay_min: float | None = Field(default=None, ge=0)
    pay_max: float | None = Field(default=None, ge=0)
    pay_unit: PayUnit | None = None
    pay_currency: CurrencyCode | None = None
    noc_code: NocCode | None = None
    posted_at: AwareDatetime | None = None
    first_seen_at: AwareDatetime
    fingerprint: str | None = None
    status: JobStatus = JobStatus.NEW
    filter_reason: str | None = None

    @model_validator(mode="after")
    def _pay_range(self) -> "Job":
        if self.pay_min is not None and self.pay_max is not None and self.pay_min > self.pay_max:
            raise ValueError("pay_min is greater than pay_max")
        return self


class Review(Record):
    """One reviewer decision (data/reviews.csv, append-only).

    A match's current status is its latest review.
    """

    match_id: NonEmptyStr
    decision: Decision
    reason_code: ReasonCode | None = None
    notes: str | None = None
    reviewer: NonEmptyStr
    reviewed_at: AwareDatetime

    @model_validator(mode="after")
    def _reject_needs_reason(self) -> "Review":
        if self.decision is Decision.REJECTED and self.reason_code is None:
            raise ValueError("a rejected match needs a reason_code")
        return self


class Run(Record):
    """Per-run statistics (data/runs.csv, append-only)."""

    run_id: NonEmptyStr
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    status: RunStatus
    source_counts: list[str] = []  # "source=count" items, e.g. "adzuna=42"
    fetched: int = Field(default=0, ge=0)
    new_jobs: int = Field(default=0, ge=0)
    filtered_out: int = Field(default=0, ge=0)
    matched: int = Field(default=0, ge=0)
    errors: int = Field(default=0, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    model_name: str | None = None
    model_version: str | None = None


class ContractorProfile(BaseModel):
    """One contractor in data/contractors/contractors.csv (built from the master workbook by
    build-contractors). Lists are "|"-joined cells. Name and profile text are personal data:
    never log them."""

    model_config = ConfigDict(extra="forbid")

    talent_id: NonEmptyStr
    talent_name: NonEmptyStr = Field(repr=False)
    location: str | None = None
    standardized_headline: str | None = None
    executive_summary: str | None = Field(default=None, repr=False)
    core_strengths: list[str] = []
    proof_points: list[str] = Field(default=[], repr=False)
    main_industry: list[str] = []
    search_keywords: list[str] = []  # the workbook's Search Keywords
    seniority: str | None = None
    weekly_hours: float | None = Field(default=None, ge=0)
    rate_expectation: NonEmptyStr  # hourly figure ("150", "52.5") or the default text
    target_titles: list[str] = []
    contract: bool = True
    # v1 (2026-11-05), optional so older contractor lists still load. Values come from the
    # workbook (later the intake form) and use config/professions.yaml names, lowercase.
    # They feed the v1 prefilter and are sent to Gemini (doc/V1_PLAN.md section 9).
    primary_profession: str | None = None
    secondary_professions: list[str] = []
    years_experience: float | None = Field(default=None, ge=0)
    languages: list[str] = []  # "en", "fr"


class LlmCallStatus(StrEnum):
    OK = "ok"
    ERROR = "error"  # bad answer or HTTP error; tokens may still have been spent
    RATE_LIMITED = "rate_limited"  # per-minute 429 after every retry
    DAILY_QUOTA = "daily_quota"  # per-day 429: nothing more until midnight Pacific time
    TIMEOUT = "timeout"  # no answer within llm.timeout_seconds (not retried; next model)
    NOT_SENT = "not_sent"  # stopped locally before sending (call too large)


class LlmUsage(Record):
    """One LLM API call in data/llm_usage.csv. Append-only. Holds counts and IDs only, never
    prompt text or contractor data. The budget guard reads it to know today's usage."""

    call_id: NonEmptyStr
    at: AwareDatetime
    model: NonEmptyStr
    purpose: NonEmptyStr  # judge, classify, intake, test
    job_ids: list[str] = []
    status: LlmCallStatus
    http_status: int | None = None
    attempts: int = Field(default=1, ge=0)  # requests sent, retries included (0: not sent)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    thinking_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    estimated_tokens: int | None = Field(default=None, ge=0)  # local estimate before the call
    note: str | None = None


class LlmVerdict(StrEnum):
    FIT = "fit"
    PARTIAL = "partial"


class LlmMatch(Record):
    """One contractor Gemini judged fit or partial for a job (data/llm_matches.csv, v1). Added by
    run-once from the cached judgment; a row is never changed. match_id = judge_key + talent
    ID, so re-runs add nothing, and a re-judged job (new text, roster, model or prompt) gets a
    new judge_key. A job's current shortlist is the rows of its newest judge_key. Reviews
    point at match_id. Everything here comes from the answer, checked locally."""

    match_id: NonEmptyStr
    job_id: NonEmptyStr
    talent_id: NonEmptyStr
    rank: int = Field(ge=1)  # 1 = best: Gemini's detailed order, then undetailed fits/partials
    verdict: LlmVerdict
    # False: Gemini judged this contractor fit or partial but did not describe them (it left
    # them out of "top"). Kept for a person to check; only the one-line reason is known.
    detailed: bool = True
    fit_score: int | None = Field(default=None, ge=0, le=100)  # for ordering only
    reason: str | None = None
    requirements_met: list[str] = []
    requirements_missing: list[str] = []
    red_flags: list[str] = []  # "kind: detail"
    evidence: list[str] = []  # "job: quote" or "contractor: quote", verified quotes only
    unverified_evidence: bool = False  # a quote was not found in the text sent and was removed
    model: NonEmptyStr
    prompt_version: NonEmptyStr
    judge_key: NonEmptyStr
    created_at: AwareDatetime
