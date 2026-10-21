from datetime import UTC, datetime
from pathlib import Path

from jobmatch.schemas import RawRecord
from jobmatch.storage.raw_store import RawStore


def record(source_id: str, content_hash: str, month: int = 10) -> RawRecord:
    return RawRecord(
        source="manual_linkedin",
        source_id=source_id,
        fetched_at=datetime(2026, month, 20, tzinfo=UTC),
        content_hash=content_hash,
        payload={"text": "Contrat de 6 mois", "fields": {"title": "Analyste"}},
    )


def test_append_is_idempotent_and_keeps_new_versions(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw", lock_timeout=5)
    assert store.append([record("1", "h1"), record("2", "h2")]) == 2
    assert store.append([record("1", "h1")]) == 0  # identical: skipped
    assert store.append([record("1", "h1-edited")]) == 1  # same job, new content: kept
    assert [r.source_id for r in store.read("manual_linkedin")] == ["1", "2", "1"]


def test_one_file_per_source_and_month(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw", lock_timeout=5)
    store.append([record("1", "h1", month=9), record("2", "h2", month=10)])
    names = sorted(p.name for p in (tmp_path / "raw").glob("*.jsonl"))
    assert names == ["manual_linkedin-2026-09.jsonl", "manual_linkedin-2026-10.jsonl"]
    line = (tmp_path / "raw" / "manual_linkedin-2026-10.jsonl").read_text(encoding="utf-8")
    assert "Contrat de 6 mois" in line  # UTF-8 kept readable
