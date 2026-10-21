"""Append-only store of postings exactly as received: data/raw/<source>-YYYY-MM.jsonl.

One JSON object per line. A record whose (source_id, content_hash) is already stored for
that source is not written again, so re-importing the same files changes nothing.
"""

import json
import os
from collections.abc import Iterable
from pathlib import Path

from filelock import FileLock

from jobmatch.schemas import RawRecord


class RawStore:
    def __init__(self, directory: Path, *, lock_timeout: float) -> None:
        self.directory = directory
        self._lock_timeout = lock_timeout

    def path_for(self, record: RawRecord) -> Path:
        return self.directory / f"{record.source}-{record.fetched_at:%Y-%m}.jsonl"

    def append(self, records: Iterable[RawRecord]) -> int:
        """Write records not stored before; return how many were written."""
        pending = list(records)
        if not pending:
            return 0
        self.directory.mkdir(parents=True, exist_ok=True)
        written = 0
        with FileLock(str(self.directory / ".raw.lock"), timeout=self._lock_timeout):
            known: dict[str, set[tuple[str, str]]] = {}
            for record in pending:
                seen = known.setdefault(record.source, self._known(record.source))
                key = (record.source_id, record.content_hash)
                if key in seen:
                    continue
                with self.path_for(record).open("a", encoding="utf-8", newline="\n") as f:
                    f.write(record.model_dump_json() + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                seen.add(key)
                written += 1
        return written

    def read(self, source: str) -> list[RawRecord]:
        records = []
        for path in sorted(self.directory.glob(f"{source}-*.jsonl")):
            with path.open(encoding="utf-8") as f:
                records.extend(
                    RawRecord.model_validate(json.loads(line)) for line in f if line.strip()
                )
        return records

    def latest(self, source: str, *, kind: str | None = None) -> dict[str, RawRecord]:
        """Most recent record per source_id (optionally only payloads of one "kind")."""
        latest: dict[str, RawRecord] = {}
        for record in self.read(source):
            if kind is not None and record.payload.get("kind") != kind:
                continue
            current = latest.get(record.source_id)
            if current is None or record.fetched_at >= current.fetched_at:
                latest[record.source_id] = record
        return latest

    def _known(self, source: str) -> set[tuple[str, str]]:
        return {(r.source_id, r.content_hash) for r in self.read(source)}
