"""Embed texts into the vector store, re-embedding only what changed.

A stored vector is reused when its id and text hash are unchanged and it was made by the
same model, model version and template. Anything else (a new or edited row, a new model,
a template change) is embedded again. Rows that are gone are dropped from the store.
The embedded texts are also written to data/embeddings/<namespace>_texts.jsonl, so a
reviewer can see exactly what was compared.
"""

import json
from collections.abc import Sequence
from pathlib import Path
from time import perf_counter

import numpy as np
from pydantic import BaseModel

from jobmatch.embed.encoder import Encoder, TextKind
from jobmatch.embed.texts import content_hash
from jobmatch.schemas import VectorSet
from jobmatch.storage.files import atomic_open
from jobmatch.storage.vector_store import NpzVectorStore, VectorStoreError


class TextItem(BaseModel):
    id: str
    text: str


class EmbedReport(BaseModel):
    namespace: str
    total: int
    embedded: int
    reused: int
    dropped: int
    model_name: str
    model_version: str
    dim: int
    seconds: float


def embed_items(
    namespace: str,
    items: Sequence[TextItem],
    *,
    kind: TextKind,
    encoder: Encoder,
    store: NpzVectorStore,
    template_hash: str,
) -> EmbedReport:
    started = perf_counter()
    info = encoder.info()
    try:
        previous = store.load(namespace)
    except VectorStoreError:
        previous = None  # unreadable or inconsistent: rebuild it
    reusable: dict[str, np.ndarray] = {}
    if (
        previous is not None
        and previous.model_name == info.model_name
        and previous.model_version == info.model_version
        and previous.text_template_hash == template_hash
        and previous.dim == info.dim
    ):
        for row, (item_id, digest) in enumerate(
            zip(previous.ids, previous.content_hashes, strict=True)
        ):
            reusable[f"{item_id}\x00{digest}"] = previous.vectors[row]

    hashes = [content_hash(item.text) for item in items]
    missing = [i for i, item in enumerate(items) if f"{item.id}\x00{hashes[i]}" not in reusable]
    fresh = encoder.encode([items[i].text for i in missing], kind) if missing else None
    vectors = np.zeros((len(items), info.dim), dtype=np.float32)
    fresh_rows = dict(zip(missing, range(len(missing)), strict=True))
    for i, item in enumerate(items):
        if i in fresh_rows:
            vectors[i] = fresh[fresh_rows[i]]  # type: ignore[index]
        else:
            vectors[i] = reusable[f"{item.id}\x00{hashes[i]}"]

    store.save(
        namespace,
        VectorSet(
            ids=[item.id for item in items],
            content_hashes=hashes,
            vectors=vectors,
            model_name=info.model_name,
            model_version=info.model_version,
            text_template_hash=template_hash,
        ),
    )
    _write_texts(store.directory / f"{namespace}_texts.jsonl", items, hashes)
    kept_ids = {item.id for item in items}
    dropped = len([i for i in (previous.ids if previous else []) if i not in kept_ids])
    return EmbedReport(
        namespace=namespace,
        total=len(items),
        embedded=len(missing),
        reused=len(items) - len(missing),
        dropped=dropped,
        model_name=info.model_name,
        model_version=info.model_version,
        dim=info.dim,
        seconds=round(perf_counter() - started, 2),
    )


def _write_texts(path: Path, items: Sequence[TextItem], hashes: list[str]) -> None:
    with atomic_open(path) as f:
        for item, digest in zip(items, hashes, strict=True):
            record = {"id": item.id, "content_hash": digest, "text": item.text}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
