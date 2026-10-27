"""A full matching run on a temporary data folder, with hand-made vectors (no model)."""

import csv
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from jobmatch.config import (
    MatchingWeights,
    Settings,
    load_normalization_rules,
    load_skills_taxonomy,
)
from jobmatch.embed.texts import (
    TEMPLATE_HASH,
    TITLE_TEMPLATE_HASH,
    TitleCleaner,
    content_hash,
    contractor_skills,
    contractor_text,
    contractor_title_items,
    job_text,
    job_title_text,
)
from jobmatch.match.export import build_rows, write_export
from jobmatch.match.run import MatchError, run_matching
from jobmatch.normalize.skills import SkillExtractor
from jobmatch.schemas import ContractorProfile, Job, JobStatus, Language, VectorSet
from jobmatch.storage.csv_store import (
    CsvJobRepository,
    CsvMatchRepository,
    CsvStore,
    to_cell,
)
from jobmatch.storage.vector_store import NpzVectorStore

PROFILES = [
    ContractorProfile(
        talent_id="TAL-A",
        talent_name="Sample Person A",
        location="Toronto, ON",
        standardized_headline="Senior Digital Project Manager",
        executive_summary="SYNTHETIC. Runs website and campaign projects.",
        core_strengths=["Project management", "Social media"],
        seniority="Senior / Lead",
        rate_expectation="150",
        target_titles=["Digital Project Manager"],
    ),
    ContractorProfile(
        talent_id="TAL-B",
        talent_name="Sample Person B",
        location="Vancouver, BC",
        standardized_headline="Junior Project Coordinator",
        executive_summary="SYNTHETIC. Coordinates schedules.",
        core_strengths=["Project management"],
        seniority="Junior",
        rate_expectation="Industry Standard",
    ),
    ContractorProfile(
        talent_id="TAL-C",
        talent_name="Sample Person C",
        location="Montréal, QC",
        standardized_headline="Rédactrice et stratège de contenu",
        executive_summary="SYNTHETIC. Rédaction bilingue.",
        rate_expectation="60",
    ),
]
# Job 1 points at A (cosine 1.0) and B (0.9); job 2 points at C. Unit vectors.
CONTRACTOR_VECTORS = np.array([[1, 0, 0], [0.9, 0.4358899, 0], [0, 1, 0]], dtype=np.float32)
JOB_VECTORS = np.array([[1, 0, 0], [0, 1, 0]], dtype=np.float32)


def write_profiles(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(ContractorProfile.model_fields)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(columns)
        for p in PROFILES:
            writer.writerow([to_cell(getattr(p, c)) for c in columns])


@pytest.fixture
def setup(
    settings: Settings, data_dir: Path, store: CsvStore, make_job: Callable[..., Job]
) -> Settings:
    contractors_csv = data_dir / "contractors" / "contractors.csv"
    write_profiles(contractors_csv)
    jobs = [
        make_job(
            job_id="manual:url-1",
            source_id="url-1",
            url="https://example.org/1",
            title_raw="Senior Digital Producer - 6 month contract",
            title_norm="senior digital producer",
            seniority="senior",
            skills=["project management", "social media"],
            requirements=["5+ years of project management in an agency"],
        ),
        make_job(
            job_id="manual:url-2",
            source_id="url-2",
            url="https://example.org/2",
            title_raw="Rédacteur ou rédactrice (contrat de 6 mois)",
            title_norm="rédacteur ou rédactrice",
            language=Language.FR,
            description_clean="Rédacteur\n\nRédiger des contenus pour le web.",
        ),
    ]
    CsvJobRepository(store).add_new(jobs)

    extractor = SkillExtractor(load_skills_taxonomy(settings.config_dir))
    model = settings.embedding.model_name
    vectors = NpzVectorStore(data_dir / "embeddings", lock_timeout=5)
    texts = [contractor_text(p, contractor_skills(p, extractor)) for p in PROFILES]
    vectors.save(
        "contractors",
        VectorSet(
            ids=[p.talent_id for p in PROFILES],
            content_hashes=[content_hash(t) for t in texts],
            vectors=CONTRACTOR_VECTORS,
            model_name=model,
            model_version="test",
            text_template_hash=TEMPLATE_HASH,
        ),
    )
    vectors.save(
        "jobs",
        VectorSet(
            ids=[j.job_id for j in jobs],
            content_hashes=[content_hash(job_text(j)) for j in jobs],
            vectors=JOB_VECTORS,
            model_name=model,
            model_version="test",
            text_template_hash=TEMPLATE_HASH,
        ),
    )
    # Title vectors point the same way as each owner's full-text vector.
    cleaner = TitleCleaner(load_normalization_rules(settings.config_dir).seniority)
    owners = {p.talent_id: row for row, p in enumerate(PROFILES)}
    items = [item for p in PROFILES for item in contractor_title_items(p, cleaner)]
    job_titles = [job_title_text(j, cleaner) for j in jobs]
    for namespace, ids, titles, matrix in (
        (
            "contractor_titles",
            [i for i, _ in items],
            [t for _, t in items],
            CONTRACTOR_VECTORS[[owners[i.rsplit("#", 1)[0]] for i, _ in items]],
        ),
        ("job_titles", [j.job_id for j in jobs], job_titles, JOB_VECTORS),
    ):
        vectors.save(
            namespace,
            VectorSet(
                ids=ids,
                content_hashes=[content_hash(t) for t in titles],
                vectors=matrix,
                model_name=model,
                model_version="test",
                text_template_hash=TITLE_TEMPLATE_HASH,
            ),
        )
    paths = settings.paths.model_copy(
        update={"data_dir": data_dir, "contractors_csv": contractors_csv}
    )
    return settings.model_copy(update={"paths": paths})


def test_passing_and_flagged_matches_go_to_separate_files(setup: Settings, store: CsvStore) -> None:
    report, results, _ = run_matching(setup)

    shortlisted = CsvMatchRepository(store).list_all()
    flagged = CsvMatchRepository(store, flagged=True).list_all()
    assert sorted((m.job_id, m.contractor_id, m.rank) for m in shortlisted) == [
        ("manual:url-1", "TAL-A", 1),
        ("manual:url-2", "TAL-C", 1),
    ]
    assert [(m.contractor_id, m.rank, m.gates_failed) for m in flagged] == [
        ("TAL-B", 2, ["seniority: under-qualified (job senior, contractor junior)"])
    ]
    a = next(m for m in shortlisted if m.contractor_id == "TAL-A")
    assert a.matched_skills == ["project management", "social media"]
    assert a.missing_must_have == []
    c = next(m for m in shortlisted if m.contractor_id == "TAL-C")
    assert c.skill_overlap is None and "posting in French" in c.flags

    statuses = {j.job_id: j.status for j in CsvJobRepository(store).list_all()}
    assert set(statuses.values()) == {JobStatus.MATCHED}
    assert (report.stored, report.flagged, report.status_changes) == (2, 1, 2)
    assert [len(r.candidates) for r in results] == [2, 1]


def test_rerun_stores_nothing_new_and_dry_run_stores_nothing(
    setup: Settings, store: CsvStore
) -> None:
    dry, _, _ = run_matching(setup, dry_run=True)
    assert dry.stored == 0 and not store.matches.path.exists()

    run_matching(setup)
    again, _, _ = run_matching(setup)
    assert (again.stored, again.flagged, again.unchanged, again.status_changes) == (0, 0, 3, 0)


def test_new_settings_add_rows_and_the_export_shows_the_newest(
    setup: Settings, store: CsvStore, tmp_path: Path
) -> None:
    first, _, _ = run_matching(setup)
    tuned = setup.matching.model_copy(
        update={"weights": MatchingWeights(cosine=0.5, skill_overlap=0.5)}
    )
    second, _, _ = run_matching(setup.model_copy(update={"matching": tuned}))
    assert first.scoring_version != second.scoring_version
    assert second.stored == 2 and len(CsvMatchRepository(store).list_all()) == 4

    rows = build_rows(
        CsvMatchRepository(store).list_all(),
        CsvMatchRepository(store, flagged=True).list_all(),
        {j.job_id: j for j in CsvJobRepository(store).list_all()},
        {p.talent_id: p for p in PROFILES},
    )
    assert len(rows) == 3
    assert {r.list for r in rows} == {"shortlist", "flagged"}
    dest = tmp_path / "exports" / "matches.csv"
    assert write_export(rows, dest) == 3
    text = dest.read_text(encoding="utf-8-sig")
    assert "Sample Person A" in text and "rédacteur ou rédactrice" in text


def test_changed_contractor_text_must_be_re_embedded(setup: Settings) -> None:
    path = setup.paths.contractors_csv
    path.write_text(
        path.read_text(encoding="utf-8").replace("Coordinates schedules", "Edited"),
        encoding="utf-8",
    )
    with pytest.raises(MatchError, match="changed since embedding. Run embed-contractors"):
        run_matching(setup)


def test_vectors_from_another_model_are_refused(setup: Settings) -> None:
    other = setup.embedding.model_copy(update={"model_name": "intfloat/multilingual-e5-small"})
    with pytest.raises(MatchError, match="made with BAAI/bge-m3, but settings use"):
        run_matching(setup.model_copy(update={"embedding": other}))


def test_missing_job_vectors_are_refused(setup: Settings) -> None:
    (setup.paths.data_dir / "embeddings" / "jobs.npz").unlink()
    with pytest.raises(MatchError, match="Run embed-jobs"):
        run_matching(setup)


def test_closest_title_is_stored(setup: Settings, store: CsvStore) -> None:
    run_matching(setup)
    a = next(m for m in CsvMatchRepository(store).list_all() if m.contractor_id == "TAL-A")
    assert a.matched_title == "digital project manager"  # "Senior" removed, duplicates merged
    assert a.title_cosine == pytest.approx(1.0)


def test_missing_title_vectors_are_refused(setup: Settings) -> None:
    (setup.paths.data_dir / "embeddings" / "contractor_titles.npz").unlink()
    with pytest.raises(
        MatchError, match="Title vectors are missing or out of date. Run embed-contractors"
    ):
        run_matching(setup)
