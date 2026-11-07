from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from jobmatch.config import Settings, load_settings
from jobmatch.normalize.pipeline import Normalizer
from jobmatch.schemas import Job
from jobmatch.storage.csv_store import CsvStore

if TYPE_CHECKING:
    from jobmatch.app.service import AppService

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
SAMPLES_DIR = PROJECT_ROOT / "data" / "samples"
POSTINGS_DIR = Path(__file__).resolve().parent / "fixtures" / "postings"


def posting(name: str) -> str:
    """Synthetic posting text from tests/fixtures/postings."""
    return (POSTINGS_DIR / name).read_text(encoding="utf-8")


def repo_settings() -> Settings:
    """Repo settings, but never the skills learned from the live contractor list in data/,
    so tests do not depend on runtime data."""
    settings = load_settings(CONFIG_DIR)
    paths = settings.paths.model_copy(
        update={"learned_skills": PROJECT_ROOT / "tests" / "no-learned-skills.yaml"}
    )
    return settings.model_copy(update={"paths": paths})


@pytest.fixture(autouse=True)
def _no_llm_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker Compose loads .env into the container. Remove the Gemini key for every test,
    so no test can ever reach the real API, even through a mistake in a fake."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture(scope="session")
def normalizer() -> Normalizer:
    return Normalizer.from_settings(repo_settings())


@pytest.fixture
def settings() -> Settings:
    return repo_settings()


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


# ---- the desktop app (tests/test_app_service.py, tests/test_app_ui.py) ----


@pytest.fixture
def app_settings(settings: Settings, data_dir: Path) -> Settings:
    """Repo settings pointed at a temp data folder; fast pacing so tests do not sleep."""
    assert settings.llm is not None
    paths = settings.paths.model_copy(
        update={
            "data_dir": data_dir,
            "exports_dir": data_dir.parent / "exports",
            "contractors_csv": data_dir / "contractors" / "contractors.csv",
        }
    )
    limits = settings.llm.limits.model_copy(update={"rpm": 60_000})
    llm = settings.llm.model_copy(update={"limits": limits})
    return settings.model_copy(update={"paths": paths, "llm": llm})


@pytest.fixture
def env() -> dict[str, str]:
    """The app service's environment (never the real one)."""
    return {}


@pytest.fixture
def service(app_settings: Settings, tmp_path: Path, env: dict[str, str]) -> "AppService":
    """The app service with a fake Gemini (test_pipeline.FakeGemini) and a temp .env."""
    from jobmatch.app.service import AppService
    from test_pipeline import FakeGemini

    fake = FakeGemini()
    return AppService(
        app_settings, tmp_path / "app.env", provider_factory=lambda: fake, environ=env
    )
