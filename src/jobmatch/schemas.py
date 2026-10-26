"""Pydantic v2 models for every record that crosses a module boundary.

For the CSV-backed records, the CSV columns are the field names in declaration order.
IDs and NOC codes are always strings (Excel strips leading zeros).
"""

from enum import StrEnum
from typing import Annotated, Any

import numpy as np
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 3  # 2: responsibilities, nice_to_have, years_experience_min; 3: commitment

_PROVINCES = "AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT"

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


class Band(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Decision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_INFO = "needs_info"


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


class Contractor(Record):
    """One contractor (data/contractors.csv, human-maintained, read-only for jobmatch).

    display_name and profile text are personal data: never log them.
    """

    contractor_id: NonEmptyStr
    display_name: NonEmptyStr = Field(repr=False)
    profile_text_raw: str | None = Field(default=None, repr=False)
    languages: list[LanguageCode] = []
    regions: list[Region] = []
    remote_preference: RemotePreference | None = None
    noc_codes: list[NocCode] = []
    rate_min: float | None = Field(default=None, ge=0)
    rate_max: float | None = Field(default=None, ge=0)
    rate_unit: PayUnit | None = None
    rate_currency: CurrencyCode | None = None

    @model_validator(mode="after")
    def _rate_range(self) -> "Contractor":
        if (
            self.rate_min is not None
            and self.rate_max is not None
            and self.rate_min > self.rate_max
        ):
            raise ValueError("rate_min is greater than rate_max")
        return self


class ContractorFacet(Record):
    """One area of expertise of a contractor (data/contractor_facets.csv, human-maintained)."""

    contractor_id: NonEmptyStr
    facet_id: NonEmptyStr
    area: NonEmptyStr
    skills: list[str] = []
    years: float | None = Field(default=None, ge=0)
    seniority: str | None = None
    domains: list[str] = []


class Match(Record):
    """One shortlisted (job, contractor, facet) pair (data/matches.csv)."""

    match_id: NonEmptyStr
    job_id: NonEmptyStr
    contractor_id: NonEmptyStr
    facet: NonEmptyStr
    cosine: float
    skill_overlap: float = Field(ge=0, le=1)
    score: float
    band: Band
    matched_skills: list[str] = []
    missing_must_have: list[str] = []
    gates_failed: list[str] = []
    created_at: AwareDatetime


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
    seniority: str | None = None
    weekly_hours: float | None = Field(default=None, ge=0)
    rate_expectation: NonEmptyStr  # hourly figure ("150", "52.5") or the default text
    target_titles: list[str] = []
    contract: bool = True


# ---- embeddings (stored as .npz plus meta.json, never in CSV) ----


class EmbeddingMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: NonEmptyStr
    model_version: NonEmptyStr
    dim: int = Field(gt=0)
    text_template_hash: NonEmptyStr
    count: int = Field(ge=0)
    updated_at: AwareDatetime


class VectorSet(BaseModel):
    """A block of float32 vectors with their ids, content hashes and model identity."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    ids: list[str]
    content_hashes: list[str]
    vectors: np.ndarray
    model_name: NonEmptyStr
    model_version: NonEmptyStr
    text_template_hash: NonEmptyStr

    @field_validator("vectors", mode="before")
    @classmethod
    def _as_float32_matrix(cls, value: Any) -> np.ndarray:
        array = np.asarray(value, dtype=np.float32)
        if array.ndim != 2 or array.shape[1] == 0:
            raise ValueError(f"vectors must be a 2-D array with dim > 0, got shape {array.shape}")
        if not np.isfinite(array).all():
            raise ValueError("vectors contain NaN or infinite values")
        return array

    @model_validator(mode="after")
    def _consistent(self) -> "VectorSet":
        rows = self.vectors.shape[0]
        if len(self.ids) != rows or len(self.content_hashes) != rows:
            raise ValueError(
                f"{rows} vectors but {len(self.ids)} ids and {len(self.content_hashes)} hashes"
            )
        if len(set(self.ids)) != len(self.ids):
            raise ValueError("ids must be unique")
        return self

    @property
    def dim(self) -> int:
        return int(self.vectors.shape[1])
