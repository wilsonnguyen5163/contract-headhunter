"""NumPy vector store: one .npz per namespace plus a shared meta.json (model, version, dim,
text-template hash, count). Vectors never go in CSV."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from filelock import FileLock
from pydantic import BaseModel, ConfigDict, ValidationError

from jobmatch.schemas import SCHEMA_VERSION, EmbeddingMeta, VectorSet
from jobmatch.storage.files import atomic_open, atomic_write_text

META_FILE = "meta.json"
_NAMESPACE = re.compile(r"^[a-z0-9_]+$")


class VectorStoreError(Exception):
    pass


class _MetaFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int
    namespaces: dict[str, EmbeddingMeta] = {}


def _check_namespace(namespace: str) -> None:
    if not _NAMESPACE.match(namespace):
        raise ValueError(f"invalid namespace {namespace!r}: use lowercase letters, digits and _")


class NpzVectorStore:
    def __init__(self, directory: Path, *, lock_timeout: float) -> None:
        self.directory = directory
        self._lock = FileLock(str(directory / ".vectors.lock"), timeout=lock_timeout)

    @property
    def meta_path(self) -> Path:
        return self.directory / META_FILE

    def _npz_path(self, namespace: str) -> Path:
        return self.directory / f"{namespace}.npz"

    def save(self, namespace: str, vectors: VectorSet) -> None:
        _check_namespace(namespace)
        self.directory.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with atomic_open(self._npz_path(namespace), binary=True) as f:
                np.savez(
                    f,
                    ids=np.asarray(vectors.ids, dtype=np.str_),
                    content_hashes=np.asarray(vectors.content_hashes, dtype=np.str_),
                    vectors=vectors.vectors,
                )
            meta = self._read_meta()
            meta.namespaces[namespace] = EmbeddingMeta(
                model_name=vectors.model_name,
                model_version=vectors.model_version,
                dim=vectors.dim,
                text_template_hash=vectors.text_template_hash,
                count=len(vectors.ids),
                updated_at=datetime.now(UTC),
            )
            atomic_write_text(self.meta_path, meta.model_dump_json(indent=2) + "\n")

    def meta(self, namespace: str) -> EmbeddingMeta | None:
        _check_namespace(namespace)
        if not self.meta_path.exists():
            return None
        return self._read_meta().namespaces.get(namespace)

    def load(self, namespace: str) -> VectorSet | None:
        _check_namespace(namespace)
        path = self._npz_path(namespace)
        if not path.exists():
            return None
        with self._lock:
            meta = self._read_meta().namespaces.get(namespace)
            if meta is None:
                raise VectorStoreError(f"{path.name} has no entry in {META_FILE}")
            with np.load(path, allow_pickle=False) as data:
                ids = [str(x) for x in data["ids"].tolist()]
                hashes = [str(x) for x in data["content_hashes"].tolist()]
                matrix = data["vectors"]
        vectors = VectorSet(
            ids=ids,
            content_hashes=hashes,
            vectors=matrix,
            model_name=meta.model_name,
            model_version=meta.model_version,
            text_template_hash=meta.text_template_hash,
        )
        if len(vectors.ids) != meta.count or vectors.dim != meta.dim:
            raise VectorStoreError(
                f"{path.name} holds {len(vectors.ids)}x{vectors.dim} vectors but {META_FILE} "
                f"says {meta.count}x{meta.dim}"
            )
        return vectors

    def validate(self) -> list[str]:
        """Return every problem found (unreadable files, count/dim mismatches, orphans)."""
        if not self.directory.exists():
            return []
        problems: list[str] = []
        try:
            namespaces = self._read_meta().namespaces if self.meta_path.exists() else {}
        except (OSError, ValueError, ValidationError, VectorStoreError) as exc:
            return [f"{META_FILE}: unreadable: {exc}"]
        for namespace in namespaces:
            try:
                if self.load(namespace) is None:
                    problems.append(
                        f"{META_FILE} lists {namespace!r} but {namespace}.npz is missing"
                    )
            except (OSError, ValueError, KeyError, VectorStoreError, ValidationError) as exc:
                problems.append(f"{namespace}.npz: {exc}")
        for npz in sorted(self.directory.glob("*.npz")):
            if npz.stem not in namespaces:
                problems.append(f"{npz.name} has no entry in {META_FILE}")
        return problems

    def _read_meta(self) -> _MetaFile:
        if not self.meta_path.exists():
            return _MetaFile(schema_version=SCHEMA_VERSION)
        meta = _MetaFile.model_validate(json.loads(self.meta_path.read_text(encoding="utf-8")))
        if meta.schema_version != SCHEMA_VERSION:
            raise VectorStoreError(
                f"{META_FILE} has schema_version {meta.schema_version}, expected {SCHEMA_VERSION}"
            )
        return meta
