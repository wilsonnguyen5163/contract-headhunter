"""Embedding texts and incremental vector building, with a fake model (no download)."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from conftest import CONFIG_DIR
from jobmatch.config import EmbeddingSettings, load_normalization_rules, load_skills_taxonomy
from jobmatch.embed.build import TextItem, embed_items
from jobmatch.embed.encoder import Encoder
from jobmatch.embed.texts import (
    TEMPLATE_HASH,
    TitleCleaner,
    contractor_skills,
    contractor_text,
    contractor_title_items,
    job_text,
    job_title_text,
    title_owner,
)
from jobmatch.normalize.skills import SkillExtractor
from jobmatch.schemas import ContractorProfile, Job
from jobmatch.storage.vector_store import NpzVectorStore

SKILLS = SkillExtractor(load_skills_taxonomy(CONFIG_DIR))


def make_profile(**fields: Any) -> ContractorProfile:
    base: dict[str, Any] = {
        "talent_id": "TAL-1",
        "talent_name": "Sample Person A",
        "location": "Calgary, AB",
        "standardized_headline": "Senior Digital & Technical Project Manager",
        "executive_summary": "SYNTHETIC. Leads website migrations and digital programs.",
        "core_strengths": ["Program delivery", "Project management", "agile/waterfall"],
        "proof_points": ["Managed 40+ staff and $10M budgets"],
        "main_industry": ["Agencies", "technology"],
        "seniority": "Senior / Lead",
        "weekly_hours": 40,
        "rate_expectation": "150",
        "target_titles": ["Sr. Digital Project Manager", "Program Manager"],
    }
    base.update(fields)
    return ContractorProfile(**base)


def make_job(**fields: Any) -> Job:
    base: dict[str, Any] = {
        "job_id": "manual:url-1",
        "source": "manual",
        "source_id": "url-1",
        "url": "https://example.org/1",
        "title_raw": "Senior Campaign Producer - 6 month FTC",
        "title_norm": "senior campaign producer",
        "company_norm": "example agency",
        "city": "Vancouver",
        "seniority": "senior",
        "skills": ["project management", "graphic design"],
        "responsibilities": ["Structure complex digital projects", "Lead content development"],
        "requirements": ["5+ years in digital project management"],
        "nice_to_have": ["Digital asset management"],
        "pay_min": 70000,
        "first_seen_at": datetime(2026, 10, 20, tzinfo=UTC),
    }
    base.update(fields)
    return Job(**base)


# ---- texts ----


def test_job_text_has_the_labels_in_order_and_no_gates() -> None:
    text = job_text(make_job())
    assert text.split("\n") == [
        "Title: senior campaign producer",
        "Seniority: senior",
        "Skills: project management; graphic design",
        "Responsibilities: Structure complex digital projects; Lead content development",
        "Requirements: 5+ years in digital project management",
        "Nice to have: Digital asset management",
    ]
    for gate in ("example agency", "Vancouver", "70000"):
        assert gate not in text


def test_job_without_lists_uses_its_description() -> None:
    job = make_job(
        responsibilities=[], requirements=[], nice_to_have=[],
        description_clean="Senior Campaign Producer\n\nDescription\nLead a 6-month rollout.",
    )  # fmt: skip
    assert job_text(job).endswith("Description: Description Lead a 6-month rollout.")


def test_contractor_text_shares_the_job_vocabulary_and_leaves_out_personal_fields() -> None:
    profile = make_profile()
    skills = contractor_skills(profile, SKILLS)
    assert "project management" in skills  # from the taxonomy, as on the job side
    text = contractor_text(profile, skills)
    lines = text.split("\n")
    assert lines[0] == "Title: Senior Digital & Technical Project Manager"
    assert lines[1] == "Target titles: Sr. Digital Project Manager; Program Manager"
    assert [line.split(":")[0] for line in lines] == [
        "Title", "Target titles", "Seniority", "Skills", "Industries", "Summary", "Proof points",
    ]  # fmt: skip
    assert text.count("roject management") == 1  # the core strength is not repeated
    for private in ("Sample Person A", "Calgary", "150", "TAL-1"):
        assert private not in text


def test_thin_contractor_profile_still_has_a_text() -> None:
    profile = make_profile(
        executive_summary=None, core_strengths=[], proof_points=[], main_industry=[],
        seniority=None, standardized_headline="Writer", target_titles=["Writer"],
    )  # fmt: skip
    assert contractor_text(profile, []) == "Title: Writer\nTarget titles: Writer"


# ---- incremental building ----


class CountingModel:
    """Fake SentenceTransformer: one distinct vector per text; counts what it encodes."""

    def __init__(self) -> None:
        self.encoded: list[str] = []

    def encode(self, sentences: list[str], **_: Any) -> np.ndarray:
        if sentences != ["probe"]:  # Encoder.info() probes once for the dimension
            self.encoded.extend(sentences)
        return np.array([[len(s), 1.0, 0.5] for s in sentences], dtype=np.float32)


@pytest.fixture
def settings_embedding(settings) -> EmbeddingSettings:  # noqa: ANN001
    return settings.embedding


def build(store: NpzVectorStore, items: list[TextItem], model: CountingModel, emb, **kw):  # noqa: ANN001
    return embed_items(
        "contractors", items, kind="facet", encoder=Encoder(emb, model=model), store=store,
        template_hash=kw.get("template_hash", TEMPLATE_HASH),
    )  # fmt: skip


def test_only_changed_texts_are_embedded_again(tmp_path: Path, settings_embedding) -> None:  # noqa: ANN001
    store = NpzVectorStore(tmp_path / "embeddings", lock_timeout=5)
    first = [TextItem(id="A", text="Title: one"), TextItem(id="B", text="Title: two")]
    model = CountingModel()
    report = build(store, first, model, settings_embedding)
    assert (report.embedded, report.reused) == (2, 0)

    second = [
        TextItem(id="A", text="Title: one"),  # unchanged
        TextItem(id="B", text="Title: two, edited"),
        TextItem(id="C", text="Title: three"),
    ]
    model = CountingModel()
    report = build(store, second, model, settings_embedding)
    assert model.encoded == ["Title: two, edited", "Title: three"]
    assert (report.total, report.embedded, report.reused) == (3, 2, 1)

    loaded = store.load("contractors")
    assert loaded is not None and loaded.ids == ["A", "B", "C"]
    texts = tmp_path / "embeddings" / "contractors_texts.jsonl"
    assert texts.read_text(encoding="utf-8").count("\n") == 3


def test_removed_rows_are_dropped_and_template_change_reembeds(
    tmp_path: Path,
    settings_embedding,  # noqa: ANN001
) -> None:
    store = NpzVectorStore(tmp_path / "embeddings", lock_timeout=5)
    items = [TextItem(id="A", text="x"), TextItem(id="B", text="y")]
    build(store, items, CountingModel(), settings_embedding)

    report = build(store, items[:1], CountingModel(), settings_embedding)
    assert (report.dropped, report.reused) == (1, 1)

    model = CountingModel()
    report = build(store, items[:1], model, settings_embedding, template_hash="other")
    assert model.encoded == ["x"]  # a new template means every text is embedded again


def test_empty_list_writes_an_empty_store(tmp_path: Path, settings_embedding) -> None:  # noqa: ANN001
    store = NpzVectorStore(tmp_path / "embeddings", lock_timeout=5)
    report = build(store, [], CountingModel(), settings_embedding)
    assert report.total == 0
    loaded = store.load("contractors")
    assert loaded is not None and loaded.vectors.shape == (0, 3)


# ---- titles ----

CLEANER = TitleCleaner(load_normalization_rules(CONFIG_DIR).seniority)


@pytest.mark.parametrize(
    ("title", "brackets", "cleaned"),
    [
        ("senior producer, cbc life (english services)", True, "producer, cbc life"),
        ("Sr. Art Director/Designer", False, "art director/designer"),
        ("Senior Creative Director & Copywriter", False, "creative director & copywriter"),
        ("Mid-level Social Media Strategist", False, "social media strategist"),
        ("Rédactrice sénior (contrat)", True, "redactrice"),
        ("Project Manager (PMP)", False, "project manager (pmp)"),  # contractor brackets kept
        ("Team Lead, Data", False, "team lead, data"),  # lead and executive words stay
        ("Senior", True, ""),
    ],
)
def test_title_cleaning(title: str, brackets: bool, cleaned: str) -> None:
    assert CLEANER.clean(title, drop_brackets=brackets) == cleaned


def test_contractor_titles_are_headline_then_targets_without_duplicates() -> None:
    profile = make_profile()  # headline "Senior Digital & Technical Project Manager"
    profile = profile.model_copy(
        update={
            "target_titles": [
                "Sr. Digital Project Manager",
                "Digital Project Manager",
                "Program Manager",
            ]
        }
    )
    assert contractor_title_items(profile, CLEANER) == [
        ("TAL-1#0", "digital & technical project manager"),
        ("TAL-1#1", "digital project manager"),
        ("TAL-1#2", "program manager"),
    ]
    assert title_owner("TAL-1#2") == "TAL-1"


def test_job_title_text_drops_brackets_and_seniority() -> None:
    job = make_job(title_norm="senior producer, cbc life (english services)")
    assert job_title_text(job, CLEANER) == "producer, cbc life"
