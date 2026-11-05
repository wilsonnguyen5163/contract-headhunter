"""Profession notes: config, assignment, verdicts and the end-to-end note lookup."""

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from jobmatch.config import Professions, Settings, load_normalization_rules, load_professions
from jobmatch.embed.texts import (
    PROFESSION_TEMPLATE_HASH,
    TITLE_TEMPLATE_HASH,
    TitleCleaner,
    content_hash,
    contractor_title_items,
    job_core_text,
    profession_text,
)
from jobmatch.match.professions import ProfessionFinder, Verdict, profession_notes
from jobmatch.schemas import ContractorProfile, Job, Language, VectorSet
from jobmatch.storage.vector_store import NpzVectorStore

RULES = Professions(
    version=1,
    min_margin=0.05,
    close=[["creative direction", "graphic design"]],
    professions={
        "recruiting": "Finds and hires people.",
        "sales": "Sells and grows revenue.",
        "creative direction": "Leads creative concepts.",
        "graphic design": "Designs visuals.",
    },
)
# One axis per profession, in RULES order.
AXES = np.eye(4, dtype=np.float32)


def test_live_professions_load_and_close_groups_name_real_professions(settings: Settings) -> None:
    live = load_professions(settings.config_dir)
    assert live is not None and len(live.professions) >= 20
    assert 0 < live.min_margin < 0.2


def test_close_group_with_an_unknown_profession_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown profession"):
        Professions(version=1, min_margin=0.02, close=[["sales", "astronaut"]],
                    professions={"sales": "Sells.", "recruiting": "Hires."})  # fmt: skip


def test_missing_file_means_no_professions(tmp_path: Path) -> None:
    assert load_professions(tmp_path) is None


def test_assign_skips_titles_that_fit_two_professions_about_equally() -> None:
    finder = ProfessionFinder(RULES, AXES)
    clear = np.array([0.9, 0.2, 0.0, 0.0])  # recruiting, well ahead of sales
    vague = np.array([0.6, 0.58, 0.0, 0.0])  # "consultant": recruiting and sales nearly tied
    assert finder.assign(np.stack([clear, vague])) == ["recruiting", None]


@pytest.mark.parametrize(
    ("job", "contractor", "verdict"),
    [
        ("recruiting", ["recruiting", None], Verdict.SAME),
        ("graphic design", ["creative direction"], Verdict.CLOSE),
        ("recruiting", ["sales"], Verdict.DIFFERENT),
        (None, ["sales"], Verdict.UNCLEAR),
        ("sales", [None, None], Verdict.UNCLEAR),
    ],
)
def test_compare_gives_a_verdict(
    job: str | None, contractor: list[str | None], verdict: Verdict
) -> None:
    assert ProfessionFinder(RULES, AXES).compare(job, contractor).verdict is verdict


def test_note_text_names_both_sides() -> None:
    note = ProfessionFinder(RULES, AXES).compare("recruiting", ["sales", None])
    assert note.text() == "job: recruiting · talent: sales (different)"


def test_job_core_is_title_and_first_responsibilities(make_job: Callable[..., Job]) -> None:
    job = make_job(
        title_raw="Senior Talent Acquisition Specialist (Contract)",
        title_norm="senior talent acquisition specialist",
        responsibilities=["Source candidates", "Run interviews", "Advise hiring managers",
                          "Report on hiring metrics"],
        requirements=["5 years of recruiting"],
    )  # fmt: skip
    assert job_core_text(job) == (
        "senior talent acquisition specialist\nSource candidates\nRun interviews\n"
        "Advise hiring managers"
    )


def test_job_core_falls_back_to_the_description_in_french(make_job: Callable[..., Job]) -> None:
    job = make_job(
        title_raw="Rédacteur web (contrat de 6 mois)",
        language=Language.FR,
        description_clean="Rédacteur web\nVous rédigerez des contenus pour notre site et nos "
        "infolettres. Vous travaillerez avec l'équipe marketing.",
    )
    core = job_core_text(job).split("\n")
    assert core[0] == "Rédacteur web (contrat de 6 mois)"
    assert core[1].startswith("Vous rédigerez des contenus")
    assert len(core) == 3


def _profile(talent_id: str, headline: str, *titles: str) -> ContractorProfile:
    return ContractorProfile(
        talent_id=talent_id,
        talent_name="Sample",
        standardized_headline=headline,
        target_titles=list(titles),
        rate_expectation="60",
    )


def _setup(
    settings: Settings, data_dir: Path, make_job: Callable[..., Job]
) -> tuple[Settings, list[Job], list[ContractorProfile]]:
    """Live profession list; synthetic vectors on one axis per profession."""
    live = load_professions(settings.config_dir)
    assert live is not None
    names = list(live.professions)
    axis = {n: np.eye(len(names), dtype=np.float32)[i] for i, n in enumerate(names)}
    jobs = [
        make_job(job_id="manual:1", title_raw="Talent Acquisition Specialist",
                 responsibilities=["Source candidates"]),
        make_job(job_id="manual:2", title_raw="Graphic Designer",
                 responsibilities=["Design social assets"]),
    ]  # fmt: skip
    profiles = [
        _profile("TAL-S", "Account Executive", "Sales Consultant"),
        _profile("TAL-D", "Creative Director"),
    ]
    model = settings.embedding.model_name
    rules = load_normalization_rules(settings.config_dir)
    cleaner = TitleCleaner(rules.seniority, rules.title_engagement_words)
    titles = [item for p in profiles for item in contractor_title_items(p, cleaner)]
    # Job 1 is recruiting, job 2 graphic design; TAL-S sells ("Sales Consultant" is vague),
    # TAL-D is creative direction.
    title_vectors = {
        "TAL-S#0": axis["sales and business development"],
        "TAL-S#1": (axis["sales and business development"] + axis["training and facilitation"]),
        "TAL-D#0": axis["creative direction"],
    }
    vectors = NpzVectorStore(data_dir / "embeddings", lock_timeout=5)
    for namespace, ids, texts, matrix, template in (
        ("professions", names, [profession_text(n, live.professions[n]) for n in names],
         np.stack([axis[n] for n in names]), PROFESSION_TEMPLATE_HASH),
        ("job_cores", [j.job_id for j in jobs], [job_core_text(j) for j in jobs],
         np.stack([axis["recruiting and talent acquisition"],
                   axis["graphic and visual design"]]), PROFESSION_TEMPLATE_HASH),
        ("contractor_titles", [i for i, _ in titles], [t for _, t in titles],
         np.stack([title_vectors[i] for i, _ in titles]), TITLE_TEMPLATE_HASH),
    ):  # fmt: skip
        vectors.save(
            namespace,
            VectorSet(
                ids=ids,
                content_hashes=[content_hash(t) for t in texts],
                vectors=matrix,
                model_name=model,
                model_version="test",
                text_template_hash=template,
            ),
        )
    paths = settings.paths.model_copy(update={"data_dir": data_dir})
    return settings.model_copy(update={"paths": paths}), jobs, profiles


def test_notes_for_every_pair(
    settings: Settings, data_dir: Path, make_job: Callable[..., Job]
) -> None:
    s, jobs, profiles = _setup(settings, data_dir, make_job)
    notes, reason = profession_notes(s, jobs, profiles)
    assert reason is None
    recruiting = notes[("manual:1", "TAL-S")]
    assert recruiting.contractor == ["sales and business development"]  # vague title skipped
    assert recruiting.verdict is Verdict.DIFFERENT
    assert notes[("manual:2", "TAL-D")].verdict is Verdict.CLOSE  # creative direction ~ design


def test_no_notes_when_a_job_changed_since_embedding(
    settings: Settings, data_dir: Path, make_job: Callable[..., Job]
) -> None:
    s, jobs, profiles = _setup(settings, data_dir, make_job)
    edited = [jobs[0].model_copy(update={"responsibilities": ["Sell software"]}), jobs[1]]
    notes, reason = profession_notes(s, edited, profiles)
    assert notes == {} and reason is not None and "embed-jobs" in reason


def test_no_notes_before_embedding(settings: Settings, data_dir: Path) -> None:
    s = settings.model_copy(
        update={"paths": settings.paths.model_copy(update={"data_dir": data_dir})}
    )
    notes, reason = profession_notes(s, [], [])
    assert notes == {} and reason is not None
