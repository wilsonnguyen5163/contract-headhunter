import json
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from jobmatch.schemas import VectorSet
from jobmatch.storage.vector_store import NpzVectorStore, VectorStoreError


def make_set(rows: int = 3, dim: int = 4) -> VectorSet:
    rng = np.random.default_rng(0)
    return VectorSet(
        ids=[f"C{i:03d}:facet" for i in range(rows)],
        content_hashes=[f"hash{i}" for i in range(rows)],
        vectors=rng.normal(size=(rows, dim)),  # float64 on purpose: stored as float32
        model_name="intfloat/multilingual-e5-small",
        model_version="unpinned+sentence-transformers-test",
        text_template_hash="tmpl1",
    )


@pytest.fixture
def vstore(tmp_path: Path) -> NpzVectorStore:
    return NpzVectorStore(tmp_path / "embeddings", lock_timeout=5)


def test_save_and_load_roundtrip(vstore: NpzVectorStore) -> None:
    original = make_set()
    vstore.save("contractor_facets", original)
    loaded = vstore.load("contractor_facets")
    assert loaded is not None
    assert loaded.ids == original.ids
    assert loaded.ids[0] == "C000:facet"
    assert loaded.content_hashes == original.content_hashes
    assert loaded.vectors.dtype == np.float32
    np.testing.assert_array_equal(loaded.vectors, original.vectors)


def test_meta_records_model_identity(vstore: NpzVectorStore) -> None:
    vstore.save("jobs", make_set(rows=2, dim=8))
    meta = vstore.meta("jobs")
    assert meta is not None
    assert (meta.model_name, meta.dim, meta.count) == ("intfloat/multilingual-e5-small", 8, 2)
    assert meta.text_template_hash == "tmpl1"


def test_missing_namespace_returns_none(vstore: NpzVectorStore) -> None:
    assert vstore.load("jobs") is None
    assert vstore.meta("jobs") is None


def test_invalid_namespace_rejected(vstore: NpzVectorStore) -> None:
    with pytest.raises(ValueError):
        vstore.load("../escape")


def test_vector_set_rejects_inconsistent_shapes() -> None:
    with pytest.raises(ValidationError):
        VectorSet(
            ids=["a", "b"],
            content_hashes=["h"],
            vectors=np.zeros((2, 4)),
            model_name="m",
            model_version="v",
            text_template_hash="t",
        )


def test_vector_set_rejects_duplicate_ids_and_nan() -> None:
    with pytest.raises(ValidationError):
        VectorSet(
            ids=["a", "a"],
            content_hashes=["h1", "h2"],
            vectors=np.zeros((2, 4)),
            model_name="m",
            model_version="v",
            text_template_hash="t",
        )
    with pytest.raises(ValidationError):
        VectorSet(
            ids=["a"],
            content_hashes=["h1"],
            vectors=np.array([[np.nan, 1.0]]),
            model_name="m",
            model_version="v",
            text_template_hash="t",
        )


def test_count_mismatch_is_detected(vstore: NpzVectorStore) -> None:
    vstore.save("jobs", make_set(rows=3))
    meta = json.loads(vstore.meta_path.read_text(encoding="utf-8"))
    meta["namespaces"]["jobs"]["count"] = 5
    vstore.meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(VectorStoreError):
        vstore.load("jobs")
    assert any("jobs.npz" in p for p in vstore.validate())


def test_orphan_npz_is_reported(vstore: NpzVectorStore) -> None:
    vstore.save("jobs", make_set())
    np.savez(vstore.directory / "orphan.npz", vectors=np.zeros((1, 2)))
    assert vstore.validate() == ["orphan.npz has no entry in meta.json"]
