"""The DataLoader factory (storage/loader.py): CSV backend, MongoDB placeholder, and the rule
that code outside storage/ never builds CSV stores itself."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conftest import PROJECT_ROOT
from jobmatch.app.__main__ import run_check
from jobmatch.config import Settings
from jobmatch.schemas import (
    ContractorProfile,
    Decision,
    LlmCallStatus,
    LlmUsage,
    Review,
)
from jobmatch.storage.loader import (
    CsvDataLoader,
    MongoDataLoader,
    StorageBackendUnavailable,
    create_loader,
)

AT = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def with_backend(settings: Settings, backend: str) -> Settings:
    storage = settings.storage.model_copy(update={"backend": backend})
    return settings.model_copy(update={"storage": storage})


def person(talent_id: str, headline: str) -> ContractorProfile:
    return ContractorProfile(
        talent_id=talent_id, talent_name=f"Person {talent_id}", rate_expectation="80",
        standardized_headline=headline,
    )  # fmt: skip


def test_csv_is_the_default_backend(app_settings: Settings) -> None:
    loader = create_loader(app_settings)
    assert isinstance(loader, CsvDataLoader) and loader.backend == "csv"
    assert loader.data_dir == app_settings.paths.data_dir
    assert loader.contractors.path == app_settings.paths.contractors_csv


def test_data_dir_override_keeps_everything_in_that_folder(
    app_settings: Settings, tmp_path: Path
) -> None:
    other = tmp_path / "elsewhere"
    loader = create_loader(app_settings, data_dir=other)
    assert isinstance(loader, CsvDataLoader)
    assert loader.data_dir == other
    assert loader.contractors.path == other / "contractors" / "contractors.csv"


def test_mongodb_is_a_clear_placeholder(app_settings: Settings) -> None:
    mongo = with_backend(app_settings, "mongodb")
    with pytest.raises(StorageBackendUnavailable, match="MongoDB storage isn't available yet"):
        create_loader(mongo)
    with pytest.raises(StorageBackendUnavailable):
        MongoDataLoader(mongo)


def test_csv_loader_round_trip(app_settings: Settings) -> None:
    loader = create_loader(app_settings)
    assert not loader.contractors.exists()
    assert loader.contractors.replace_all([person("TAL-1", "Data Analyst")]) is None  # new list
    kept = loader.contractors.replace_all(
        [person("TAL-1", "Data Analyst"), person("TAL-2", "Analyste de données")]
    )
    assert kept is not None and Path(kept).exists()  # the previous list, kept first
    assert [p.talent_id for p in loader.contractors.list_all()] == ["TAL-1", "TAL-2"]

    usage = LlmUsage(call_id="c1", at=AT, model="m", purpose="judge", status=LlmCallStatus.OK)
    loader.llm_usage.record(usage)
    assert loader.llm_usage.list_all() == [usage]

    review = Review(match_id="m1", decision=Decision.APPROVED, reviewer="r", reviewed_at=AT)
    loader.reviews.append(review)
    assert loader.reviews.latest_by_match() == {"m1": review}
    assert loader.jobs.list_all() == [] and loader.runs.list_all() == []


def test_run_once_explains_an_unavailable_backend(app_settings: Settings) -> None:
    from jobmatch.pipeline import run_once

    with pytest.raises(StorageBackendUnavailable, match="storage.backend"):
        run_once(with_backend(app_settings, "mongodb"), provider_factory=lambda: None)


def test_app_check_fails_on_an_unavailable_backend(tmp_path: Path) -> None:
    config = tmp_path / "config"
    import shutil

    shutil.copytree(PROJECT_ROOT / "config", config)
    settings_yaml = config / "settings.yaml"
    text = settings_yaml.read_text(encoding="utf-8").replace("  backend: csv", "  backend: mongodb")
    settings_yaml.write_text(text, encoding="utf-8")
    assert run_check(config, tmp_path) == 1
    assert "MongoDB storage isn't available yet" in (tmp_path / "check.txt").read_text("utf-8")


def test_only_storage_builds_csv_stores() -> None:
    """Code outside storage/ gets repositories from create_loader. Allowed exceptions:
    validate-data (a CSV file checker by design)."""
    pattern = re.compile(r"\bCsv(Store|\w+Repository)\(")
    allowed = {"src/jobmatch/cli.py": 1}  # validate-data's CsvStore(...)
    found: dict[str, int] = {}
    for path in (PROJECT_ROOT / "src" / "jobmatch").rglob("*.py"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel.startswith("src/jobmatch/storage/"):
            continue
        hits = len(pattern.findall(path.read_text(encoding="utf-8")))
        if hits:
            found[rel] = hits
    assert found == allowed
