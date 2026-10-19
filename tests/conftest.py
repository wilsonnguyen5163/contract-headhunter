from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from jobmatch.config import Settings, load_settings
from jobmatch.normalize.pipeline import Normalizer
from jobmatch.schemas import Job
from jobmatch.storage.csv_store import CsvStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
SAMPLES_DIR = PROJECT_ROOT / "data" / "samples"
POSTINGS_DIR = Path(__file__).resolve().parent / "fixtures" / "postings"


def posting(name: str) -> str:
    """Synthetic posting text from tests/fixtures/postings."""
    return (POSTINGS_DIR / name).read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def normalizer() -> Normalizer:
    return Normalizer.from_settings(load_settings(CONFIG_DIR))


@pytest.fixture
def settings() -> Settings:
    return load_settings(CONFIG_DIR)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def store(data_dir: Path) -> CsvStore:
    return CsvStore(data_dir, lock_timeout=5)


@pytest.fixture
def make_job() -> Callable[..., Job]:
    def factory(**overrides: Any) -> Job:
        fields: dict[str, Any] = {
            "job_id": "adzuna:0042",
            "source": "adzuna",
            "source_id": "0042",
            "url": "https://example.org/jobs/42",
            "title_raw": "Senior Python Developer (Contract)",
            "first_seen_at": datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
        }
        fields.update(overrides)
        return Job(**fields)

    return factory
