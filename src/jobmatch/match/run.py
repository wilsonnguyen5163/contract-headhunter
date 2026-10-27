"""One matching run: stored vectors -> shortlists -> matches.csv / matches_flagged.csv.

No model is loaded: jobs and contractors were embedded beforehand (embed-jobs,
embed-contractors), each as full texts plus titles. The run refuses to score when the vector
sets come from different models or templates, or when a row's text or title changed since
it was embedded, because the scores would be silently wrong. It holds data/.run.lock, and
re-running is safe: match ids are stable for the same scoring version, so identical rows
are not stored twice.
"""

import hashlib
from datetime import UTC, datetime
from time import perf_counter

import numpy as np
from filelock import FileLock
from pydantic import BaseModel, ConfigDict

from jobmatch.config import (
    MatchingSettings,
    Settings,
    load_industries,
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
    title_owner,
)
from jobmatch.ingest import RUN_LOCK_FILE
from jobmatch.match.fields import IndustryFinder, skill_categories
from jobmatch.match.scorer import (
    Candidate,
    ContractorFacts,
    JobFacts,
    TitleMatch,
    rank_all,
    select,
)
from jobmatch.normalize.must_have import MustHaveReader
from jobmatch.normalize.seniority import SeniorityDetector
from jobmatch.normalize.skills import SkillExtractor
from jobmatch.schemas import ContractorProfile, Job, JobStatus, Match, VectorSet
from jobmatch.storage.csv_store import (
    CsvContractorProfileRepository,
    CsvJobRepository,
    CsvMatchRepository,
    CsvStore,
)
from jobmatch.storage.vector_store import NpzVectorStore

ACTIVE_STATUSES = (JobStatus.NEW, JobStatus.MATCHED, JobStatus.NO_MATCH)
FACET = "profile"  # one vector per contractor for now; per-specialty facets may come later
MATCHER_VERSION = "2026-10-26.v2"  # bump when the scoring code changes


class MatchError(Exception):
    """The run cannot score safely; the message says which command fixes it."""


class JobResult(BaseModel):
    job_id: str
    title: str
    candidates: list[Candidate]


class MatchReport(BaseModel):
    scoring_version: str
    jobs: int
    contractors: int
    matched: int  # jobs with at least one match that passed every check
    no_match: int
    stored: int  # new rows in matches.csv
    flagged: int  # new rows in matches_flagged.csv
    unchanged: int  # rows already stored by an earlier identical run
    status_changes: int
    seconds: float
    dry_run: bool


def scoring_version(vectors: VectorSet, settings: MatchingSettings) -> str:
    """Changes whenever the model, the text template, the matcher or a matching setting does."""
    parts = (
        MATCHER_VERSION,
        vectors.model_name,
        vectors.model_version,
        vectors.text_template_hash,
        TITLE_TEMPLATE_HASH,
        settings.model_dump_json(),
    )
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:12]


def match_id(job_id: str, contractor_id: str, version: str) -> str:
    key = f"{job_id}\x00{contractor_id}\x00{FACET}\x00{version}"
    return "m-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _check_vectors(
    jobs: VectorSet | None, contractors: VectorSet | None, model: str
) -> tuple[VectorSet, VectorSet]:
    if contractors is None:
        raise MatchError("No contractor vectors yet. Run embed-contractors first.")
    if jobs is None:
        raise MatchError("No job vectors yet. Run embed-jobs first.")
    for name, vs in (("contractor", contractors), ("job", jobs)):
        if vs.model_name != model:
            raise MatchError(
                f"The {name} vectors were made with {vs.model_name}, but settings use {model}. "
                f"Run embed-contractors and embed-jobs again."
            )
    same = (
        jobs.model_version == contractors.model_version
        and jobs.text_template_hash == contractors.text_template_hash
        and jobs.dim == contractors.dim
    )
    if not same:
        raise MatchError(
            "Job and contractor vectors come from different model versions or text templates. "
            "Run embed-contractors and embed-jobs again."
        )
    if jobs.text_template_hash != TEMPLATE_HASH:
        raise MatchError(
            "The embedding text template changed. Run embed-contractors and embed-jobs again."
        )
    return jobs, contractors


def _check_titles(titles: VectorSet | None, main: VectorSet, command: str) -> VectorSet:
    """Title vectors must exist and come from the same model as the full-text vectors."""
    if (
        titles is None
        or titles.model_name != main.model_name
        or titles.model_version != main.model_version
        or titles.text_template_hash != TITLE_TEMPLATE_HASH
    ):
        raise MatchError(f"Title vectors are missing or out of date. Run {command} first.")
    return titles


def _closest_titles(
    job_titles: np.ndarray, title_vs: VectorSet, title_texts: dict[str, str], ids: list[str]
) -> list[list[TitleMatch | None]]:
    """For each job title row and each contractor id: the contractor's closest title."""
    rows = _rows(title_vs, title_texts, "embed-contractors", "contractor title(s)")
    sims = _unit(job_titles) @ _unit(title_vs.vectors[rows]).T
    columns: dict[str, list[int]] = {}
    for col, item_id in enumerate(title_texts):
        columns.setdefault(title_owner(item_id), []).append(col)
    names = list(title_texts.values())
    out: list[list[TitleMatch | None]] = []
    for row in sims:
        best: list[TitleMatch | None] = []
        for contractor_id in ids:
            cols = columns.get(contractor_id)
            if not cols:
                best.append(None)
                continue
            col = max(cols, key=lambda c: row[c])
            best.append(TitleMatch(cosine=round(float(row[col]), 4), title=names[col]))
        out.append(best)
    return out


def _rows(vs: VectorSet, texts: dict[str, str], command: str, what: str) -> list[int]:
    """Vector row of each id, in order; fails if one is missing or its text has changed."""
    index = {item_id: row for row, item_id in enumerate(vs.ids)}
    missing = [i for i in texts if i not in index]
    stale = [
        i for i in texts if i in index and vs.content_hashes[index[i]] != content_hash(texts[i])
    ]
    if missing or stale:
        raise MatchError(
            f"{len(missing)} {what} without a vector and {len(stale)} changed since embedding. "
            f"Run {command} first."
        )
    return [index[i] for i in texts]


def _unit(matrix: np.ndarray) -> np.ndarray:
    """Rows scaled to length 1, so a dot product is the cosine (already true when
    normalize_embeddings is on)."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1, norms)


def _to_match(job_id: str, c: Candidate, version: str, now: datetime) -> Match:
    return Match(
        match_id=match_id(job_id, c.contractor_id, version),
        job_id=job_id,
        contractor_id=c.contractor_id,
        facet=FACET,
        cosine=c.cosine,
        title_cosine=c.title_cosine,
        matched_title=c.matched_title,
        skill_overlap=c.skill_overlap,
        score=c.score,
        band=c.band,
        rank=c.rank,
        matched_skills=c.matched_skills,
        missing_must_have=c.missing_must_have,
        gates_failed=c.gates_failed,
        flags=c.flags,
        scoring_version=version,
        created_at=now,
    )


class ScoredJobs(BaseModel):
    """Every active job scored against every contractor (the full ranking, not only the
    shortlist), with what is needed to store or evaluate it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    jobs: list[Job]
    profiles: list[ContractorProfile]
    ranked: dict[str, list[Candidate]]  # job_id -> all contractors, best first
    version: str


def _score_all(settings: Settings, store: CsvStore) -> ScoredJobs:
    timeout = settings.storage.lock_timeout_seconds
    vectors = NpzVectorStore(settings.paths.data_dir / "embeddings", lock_timeout=timeout)
    job_vs, con_vs = _check_vectors(
        vectors.load("jobs"), vectors.load("contractors"), settings.embedding.model_name
    )
    job_title_vs = _check_titles(vectors.load("job_titles"), job_vs, "embed-jobs")
    con_title_vs = _check_titles(vectors.load("contractor_titles"), con_vs, "embed-contractors")

    path = settings.paths.contractors_csv
    if not path.exists():
        raise MatchError(f"Contractor list not found: {path}. Run build-contractors first.")
    profiles = CsvContractorProfileRepository(path, lock_timeout=timeout).list_all()
    taxonomy = load_skills_taxonomy(settings.config_dir, settings.paths.learned_skills)
    extractor = SkillExtractor(taxonomy)
    ignore = settings.matching.field_ignore_categories
    industries = IndustryFinder(load_industries(settings.config_dir))
    rules = load_normalization_rules(settings.config_dir)
    seniority = SeniorityDetector(rules.seniority)
    cleaner = TitleCleaner(rules.seniority)
    must_haves = MustHaveReader(extractor, rules.must_have_soft_markers)
    facts: list[ContractorFacts] = []
    texts: dict[str, str] = {}
    title_texts: dict[str, str] = {}
    for p in profiles:
        skills = contractor_skills(p, extractor)
        texts[p.talent_id] = contractor_text(p, skills)
        title_texts.update(contractor_title_items(p, cleaner))
        facts.append(
            ContractorFacts(
                profile=p,
                skills=frozenset(skills),
                levels=tuple(seniority.levels(p.seniority)),
                categories=frozenset(skill_categories(skills, taxonomy, ignore)),
                industries=industries.in_contractor(p.main_industry),
            )
        )
    con_matrix = con_vs.vectors[_rows(con_vs, texts, "embed-contractors", "contractor(s)")]

    jobs = [j for j in CsvJobRepository(store).list_all() if j.status in ACTIVE_STATUSES]
    job_rows = _rows(job_vs, {j.job_id: job_text(j) for j in jobs}, "embed-jobs", "job(s)")
    cosines = _unit(job_vs.vectors[job_rows]) @ _unit(con_matrix).T
    job_titles = {j.job_id: t for j in jobs if (t := job_title_text(j, cleaner))}
    title_rows = _rows(job_title_vs, job_titles, "embed-jobs", "job title(s)")
    closest = _closest_titles(
        job_title_vs.vectors[title_rows],
        con_title_vs,
        title_texts,
        [p.talent_id for p in profiles],
    )
    title_row = {job_id: i for i, job_id in enumerate(job_titles)}

    ranked: dict[str, list[Candidate]] = {}
    for i, job in enumerate(jobs):
        must_have = tuple(must_haves.read(job.requirements))
        titles = closest[title_row[job.job_id]] if job.job_id in title_row else None
        job_text_for_industry = " ".join(
            t
            for t in (job.title_norm or job.title_raw, job.company_raw, job.description_clean)
            if t
        )
        job_facts = JobFacts(
            job=job,
            must_have=must_have,
            categories=skill_categories(job.skills, taxonomy, ignore),
            industries=industries.in_job(job_text_for_industry),
        )
        ranked[job.job_id] = rank_all(job_facts, facts, cosines[i], settings.matching, titles)
    return ScoredJobs(
        jobs=jobs,
        profiles=profiles,
        ranked=ranked,
        version=scoring_version(con_vs, settings.matching),
    )


def score_all_jobs(settings: Settings) -> ScoredJobs:
    """Read-only: the full ranking of every active job, for evaluation."""
    data_dir = settings.paths.data_dir
    timeout = settings.storage.lock_timeout_seconds
    with FileLock(str(data_dir / RUN_LOCK_FILE), timeout=timeout):
        return _score_all(settings, CsvStore(data_dir, lock_timeout=timeout))


def run_matching(
    settings: Settings, *, dry_run: bool = False
) -> tuple[MatchReport, list[JobResult], dict[str, ContractorProfile]]:
    """Score every active job. Returns the report, each job's shortlist (for display) and
    the contractor profiles by id. Names stay in the profiles; never log them."""
    started = perf_counter()
    data_dir = settings.paths.data_dir
    timeout = settings.storage.lock_timeout_seconds
    with FileLock(str(data_dir / RUN_LOCK_FILE), timeout=timeout):
        store = CsvStore(data_dir, lock_timeout=timeout)
        scored = _score_all(settings, store)
        version = scored.version
        now = datetime.now(UTC)
        results: list[JobResult] = []
        passed: list[Match] = []
        flagged: list[Match] = []
        statuses: dict[str, tuple[JobStatus, str | None]] = {}
        for job in scored.jobs:
            picked = select(scored.ranked[job.job_id], settings.matching)
            results.append(
                JobResult(
                    job_id=job.job_id, title=job.title_norm or job.title_raw, candidates=picked
                )
            )
            for c in picked:
                (flagged if c.flagged else passed).append(_to_match(job.job_id, c, version, now))
            status = JobStatus.MATCHED if any(not c.flagged for c in picked) else JobStatus.NO_MATCH
            if status != job.status:
                statuses[job.job_id] = (status, None)

        stored = new_flagged = 0
        if not dry_run:
            stored = len(CsvMatchRepository(store).add_many(passed))
            new_flagged = len(CsvMatchRepository(store, flagged=True).add_many(flagged))
            CsvJobRepository(store).update_statuses(statuses)

    matched = sum(1 for r in results if any(not c.flagged for c in r.candidates))
    report = MatchReport(
        scoring_version=version,
        jobs=len(scored.jobs),
        contractors=len(scored.profiles),
        matched=matched,
        no_match=len(scored.jobs) - matched,
        stored=stored,
        flagged=new_flagged,
        unchanged=0 if dry_run else len(passed) + len(flagged) - stored - new_flagged,
        status_changes=len(statuses),
        seconds=round(perf_counter() - started, 2),
        dry_run=dry_run,
    )
    return report, results, {p.talent_id: p for p in scored.profiles}
