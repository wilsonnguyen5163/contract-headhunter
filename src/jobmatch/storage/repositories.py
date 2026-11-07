"""Repository interfaces, and the DataLoader that hands them out.

Code outside storage/ asks storage.loader.create_loader(settings) for a DataLoader and only
uses these interfaces, so the CSV files can later be swapped for MongoDB (or SQLite) by
changing `storage.backend` in settings.yaml, without touching callers."""

from collections.abc import Iterable, Mapping
from typing import Protocol

from jobmatch.schemas import (
    ContractorProfile,
    Job,
    JobStatus,
    LlmMatch,
    LlmUsage,
    Review,
    Run,
)


class JobRepository(Protocol):
    def list_all(self) -> list[Job]: ...

    def get(self, job_id: str) -> Job | None: ...

    def add_new(self, jobs: Iterable[Job]) -> list[Job]:
        """Store jobs not seen before (unique on source + source_id); return those added."""
        ...

    def update_statuses(self, updates: Mapping[str, tuple[JobStatus, str | None]]) -> None: ...

    def replace(self, jobs: Iterable[Job]) -> None:
        """Replace stored jobs with the same job_id (e.g. a stub filled with details)."""
        ...


class RunRepository(Protocol):
    """Append-only per-run statistics."""

    def append(self, run: Run) -> None: ...

    def list_all(self) -> list[Run]: ...


class ContractorProfileRepository(Protocol):
    """The app's contractor list (built from the workbook or upserted from a loaded file)."""

    def exists(self) -> bool:
        """Whether a contractor list has been stored yet."""
        ...

    def list_all(self) -> list[ContractorProfile]: ...

    def replace_all(self, profiles: Iterable[ContractorProfile]) -> str | None:
        """Store this list in place of the current one, keeping a copy of the old one first.
        Returns where that copy is (for the report), or None when there was nothing yet."""
        ...


class LlmMatchRepository(Protocol):
    """Append-only: Gemini's shortlists (v1)."""

    def list_all(self) -> list[LlmMatch]: ...

    def add_many(self, matches: Iterable[LlmMatch]) -> list[LlmMatch]:
        """Store matches whose match_id is new; return those added."""
        ...

    def current(self, judge_keys: Mapping[str, str]) -> dict[str, list[LlmMatch]]: ...


class ReviewRepository(Protocol):
    """Append-only: every decision is kept as evaluation data."""

    def append(self, review: Review) -> None: ...

    def list_all(self) -> list[Review]: ...

    def latest_by_match(self) -> dict[str, Review]: ...


class UsageRepository(Protocol):
    """Append-only: one row per LLM API call (token counts only), read by the budget guard."""

    def record(self, usage: LlmUsage) -> None: ...

    def list_all(self) -> list[LlmUsage]: ...


class DataLoader(Protocol):
    """Every repository for one storage backend (storage.loader.create_loader)."""

    backend: str
    jobs: JobRepository
    contractors: ContractorProfileRepository
    llm_matches: LlmMatchRepository
    reviews: ReviewRepository
    runs: RunRepository
    llm_usage: UsageRepository
