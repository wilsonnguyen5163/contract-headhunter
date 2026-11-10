"""DataLoader factory: one place that decides where records are kept.

    loader = create_loader(settings)        # storage.backend in settings.yaml
    loader.jobs.list_all()
    loader.contractors.replace_all(profiles)
    loader.llm_usage.record(usage)

Backends:
- "csv" (default): the CSV files under data/ (storage/csv_store.py), as before.
- "mongodb": a placeholder. Creating it fails with a plain message until hosting is decided
  (a local server is not an option for the desktop app, and a hosted one needs CLAUDE.md
  rule 4 and a privacy decision). No MongoDB library is installed.

Not behind the loader (files, on purpose for now): the raw page cache (data/raw/), Gemini's
cached answers (data/llm_results/) and llm-preview folders. They are caches and working
files, not records.
"""

from pathlib import Path

from jobmatch.config import Settings
from jobmatch.storage.csv_store import (
    CsvAppendRepository,
    CsvContractorProfileRepository,
    CsvJobRepository,
    CsvLlmMatchRepository,
    CsvReviewRepository,
    CsvRunRepository,
    CsvStore,
    CsvUsageRepository,
)
from jobmatch.storage.repositories import DataLoader

CONTRACTORS_DEFAULT = Path("contractors") / "contractors.csv"  # under data_dir


class StorageBackendUnavailable(RuntimeError):
    """The configured storage backend cannot be used (yet)."""


class CsvDataLoader:
    """Every repository backed by the CSV files under one data folder."""

    backend = "csv"

    def __init__(
        self, data_dir: Path, *, contractors_csv: Path | None = None, lock_timeout: float
    ) -> None:
        self.data_dir = data_dir
        self.store = CsvStore(data_dir, lock_timeout=lock_timeout)
        self.jobs = CsvJobRepository(self.store)
        self.contractors = CsvContractorProfileRepository(
            contractors_csv or data_dir / CONTRACTORS_DEFAULT, lock_timeout=lock_timeout
        )
        self.llm_matches = CsvLlmMatchRepository(self.store)
        self.reviews = CsvReviewRepository(self.store)
        self.runs = CsvRunRepository(self.store)
        self.llm_usage = CsvUsageRepository(self.store)
        self.job_scans = CsvAppendRepository(self.store.job_scans)
        self.job_rounds = CsvAppendRepository(self.store.job_rounds)
        self.applications = CsvAppendRepository(self.store.applications)
        self.talent_availability = CsvAppendRepository(self.store.talent_availability)
        self.contractor_changes = CsvAppendRepository(self.store.contractor_changes)
        self.removed_jobs = CsvAppendRepository(self.store.removed_jobs)


class MongoDataLoader:
    """Placeholder for a MongoDB backend. Not available yet: see the module docstring."""

    backend = "mongodb"

    def __init__(self, settings: Settings) -> None:
        raise StorageBackendUnavailable(
            "MongoDB storage isn't available yet: set storage.backend to csv in "
            "config/settings.yaml."
        )


def create_loader(settings: Settings, *, data_dir: Path | None = None) -> DataLoader:
    """The DataLoader for settings.storage.backend. `data_dir` overrides paths.data_dir
    (CSV only; for commands with a --data-dir option)."""
    backend = settings.storage.backend
    if backend == "csv":
        return CsvDataLoader(
            data_dir or settings.paths.data_dir,
            contractors_csv=None if data_dir else settings.paths.contractors_csv,
            lock_timeout=settings.storage.lock_timeout_seconds,
        )
    if backend == "mongodb":
        return MongoDataLoader(settings)
    raise StorageBackendUnavailable(f"unknown storage backend: {backend!r}")
