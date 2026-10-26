"""Repository interfaces. Matching and review code depend only on these, so the CSV/NumPy
implementations can later be swapped for SQLite or MongoDB without touching callers."""

from collections.abc import Iterable, Mapping
from typing import Protocol

from jobmatch.schemas import (
    Contractor,
    ContractorFacet,
    ContractorProfile,
    EmbeddingMeta,
    Job,
    JobStatus,
    Match,
    Review,
    Run,
    VectorSet,
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


class ContractorRepository(Protocol):
    """Read-only: contractor data is maintained by humans."""

    def list_contractors(self) -> list[Contractor]: ...

    def list_facets(self) -> list[ContractorFacet]: ...


class ContractorProfileRepository(Protocol):
    """Read-only: the contractor list built from the master workbook."""

    def list_all(self) -> list[ContractorProfile]: ...


class MatchRepository(Protocol):
    def list_all(self) -> list[Match]: ...

    def list_for_job(self, job_id: str) -> list[Match]: ...

    def add_many(self, matches: Iterable[Match]) -> list[Match]:
        """Store matches whose match_id is new; return those added."""
        ...


class ReviewRepository(Protocol):
    """Append-only: every decision is kept as evaluation data."""

    def append(self, review: Review) -> None: ...

    def list_all(self) -> list[Review]: ...

    def latest_by_match(self) -> dict[str, Review]: ...


class VectorStore(Protocol):
    def save(self, namespace: str, vectors: VectorSet) -> None:
        """Replace all vectors of a namespace (e.g. "jobs", "contractor_facets")."""
        ...

    def load(self, namespace: str) -> VectorSet | None: ...

    def meta(self, namespace: str) -> EmbeddingMeta | None: ...
