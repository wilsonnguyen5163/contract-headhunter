"""jobmatch command line. Run with `python -m jobmatch <command>`."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from pydantic import ValidationError

from jobmatch.config import (
    Settings,
    load_canada_locations,
    load_company_aliases,
    load_contract_phrases,
    load_industries,
    load_normalization_rules,
    load_settings,
    load_skills_taxonomy,
    load_sources,
)
from jobmatch.logging_setup import configure_logging
from jobmatch.storage.csv_store import CsvStore
from jobmatch.storage.vector_store import NpzVectorStore

if TYPE_CHECKING:
    from jobmatch.config import LlmSettings
    from jobmatch.embed.build import EmbedReport
    from jobmatch.ingest import IngestReport
    from jobmatch.llm.anonymize import Roster
    from jobmatch.llm.gemini import GeminiProvider
    from jobmatch.schemas import ContractorProfile, Job, ReviewedMatch
    from jobmatch.storage.csv_store import CsvTable

app = typer.Typer(no_args_is_help=True, add_completion=False)
log = logging.getLogger("jobmatch.cli")


@app.callback()
def main(
    ctx: typer.Context,
    config_dir: Annotated[
        Path | None,
        typer.Option(
            "--config-dir",
            help="Folder with settings.yaml (default: $JOBMATCH_CONFIG_DIR or ./config).",
        ),
    ] = None,
) -> None:
    settings = load_settings(config_dir)
    configure_logging(settings.logging.level)
    ctx.obj = settings


@app.command("validate-data")
def validate_data(
    ctx: typer.Context,
    data_dir: Annotated[
        Path | None,
        typer.Option("--data-dir", help="Data folder to check (default: from settings)."),
    ] = None,
) -> None:
    """Check config, every CSV and the embedding files against their schemas."""
    settings: Settings = ctx.obj
    root = data_dir or settings.paths.data_dir
    problems: list[str] = []

    for loader in (
        load_skills_taxonomy,
        load_contract_phrases,
        load_company_aliases,
        load_normalization_rules,
        load_canada_locations,
        load_industries,
        load_sources,
    ):
        try:
            loader(settings.config_dir)
        except (OSError, ValueError, ValidationError) as exc:
            problems.append(f"config ({loader.__name__}): {exc}")

    store = CsvStore(root, lock_timeout=settings.storage.lock_timeout_seconds)
    for table in store.all_tables():
        if not table.path.exists():
            typer.echo(f"-- {table.path.name}: not present")
            continue
        rows, errors = table.check()
        problems.extend(str(e) for e in errors)
        status = "OK" if not errors else f"{len(errors)} problem(s)"
        typer.echo(f"{status:>14} {table.path.name}: {len(rows)} valid row(s)")

    vectors = NpzVectorStore(
        root / "embeddings", lock_timeout=settings.storage.lock_timeout_seconds
    )
    problems.extend(f"embeddings/{p}" for p in vectors.validate())

    if problems:
        typer.echo(f"\n{len(problems)} problem(s):")
        for problem in problems:
            typer.echo(f"  - {problem}")
        raise typer.Exit(1)
    typer.echo("\nAll data valid.")


@app.command("smoke-embed")
def smoke_embed(ctx: typer.Context) -> None:
    """Load the embedding model and encode a few synthetic EN/FR texts (may download the model)."""
    from jobmatch.embed.smoke import run_smoke

    settings: Settings = ctx.obj
    result = run_smoke(settings.embedding)
    log.info("smoke_embed", extra={"fields": result.model_dump()})
    typer.echo(f"model:          {result.model_name} ({result.model_version})")
    typer.echo(f"dimension:      {result.dim}")
    typer.echo(f"load time:      {result.load_seconds} s")
    typer.echo(f"encode time:    {result.encode_seconds} s ({result.ms_per_text} ms/text)")
    rss = "n/a" if result.peak_rss_mb is None else f"{result.peak_rss_mb:.0f} MB"
    typer.echo(f"peak memory:    {rss}")
    for pair, value in result.similarities.items():
        typer.echo(f"  {pair:<28} {value:.4f}")
    if not result.passed:
        typer.echo("FAILED: a job was closer to the unrelated facet than to the related one.")
        raise typer.Exit(1)
    typer.echo("PASSED")


def _not_built(phase: int) -> None:
    typer.echo(f"Not built yet (planned for phase {phase}).")
    raise typer.Exit(2)


@app.command("run-once")
def run_once() -> None:
    """Fetch, normalize, embed, match and store (phase 6)."""
    _not_built(6)


_OUTCOME_LABELS = {
    "new": "new",
    "needs_details": "needs details",
    "filtered": "filtered out",
    "duplicate": "duplicate",
    "filled_stub": "stub completed",
    "already_stored": "already stored",
    "error": "ERROR",
    "skipped": "skipped",
}


@app.command("ingest-manual")
def ingest_manual(
    ctx: typer.Context,
    paths: Annotated[
        list[Path],
        typer.Argument(help="Paste files (.txt) or folders of them, e.g. data/inbox/manual"),
    ],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would happen; write nothing.")
    ] = False,
    data_dir: Annotated[
        Path | None,
        typer.Option("--data-dir", help="Data folder to write to (default: from settings)."),
    ] = None,
) -> None:
    """Import manually pasted job postings: normalize, dedupe, store in jobs.csv."""
    from jobmatch.ingest import ingest_paste_files
    from jobmatch.normalize.pipeline import Normalizer

    settings: Settings = ctx.obj
    report = ingest_paste_files(
        paths,
        data_dir=data_dir or settings.paths.data_dir,
        lock_timeout=settings.storage.lock_timeout_seconds,
        normalizer=Normalizer.from_settings(settings),
        dry_run=dry_run,
    )
    _print_report(report, dry_run=dry_run, unit="file(s)")


@app.command("ingest-links")
def ingest_links(
    ctx: typer.Context,
    paths: Annotated[
        list[Path],
        typer.Argument(
            help="Link files (.txt, one URL per line) or folders, e.g. data/inbox/links"
        ),
    ],
    refresh: Annotated[
        bool,
        typer.Option("--refresh", help="Fetch again even if a link was fetched before."),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Fetch and show what would happen; write nothing.")
    ] = False,
    data_dir: Annotated[
        Path | None,
        typer.Option("--data-dir", help="Data folder to write to (default: from settings)."),
    ] = None,
) -> None:
    """Fetch the posting links you added, then normalize, dedupe and store them in jobs.csv.
    Links fetched before are read from data/raw/ (no network) unless --refresh."""
    from jobmatch.ingest import ingest_links as run_ingest_links
    from jobmatch.normalize.pipeline import Normalizer
    from jobmatch.sources.web import WebFetcher

    settings: Settings = ctx.obj
    fetcher = WebFetcher(load_sources(settings.config_dir).web)
    try:
        report = run_ingest_links(
            paths,
            data_dir=data_dir or settings.paths.data_dir,
            lock_timeout=settings.storage.lock_timeout_seconds,
            normalizer=Normalizer.from_settings(settings),
            fetcher=fetcher,
            dry_run=dry_run,
            refresh=refresh,
        )
    finally:
        fetcher.close()
    typer.echo(f"Pages fetched from the network: {report.fetched}")
    _print_report(report, dry_run=dry_run, unit="link(s)")


def _print_report(report: "IngestReport", *, dry_run: bool, unit: str) -> None:
    header = "Dry run: nothing was written." if dry_run else f"Run {report.run_id}"
    typer.echo(header)
    for item in report.outcomes:
        label = _OUTCOME_LABELS[item.outcome]
        extra = " · ".join(part for part in (item.job_id, item.detail) if part)
        typer.echo(f"  {label:<15} {Path(item.file).name:<40} {extra}")
    summary = ", ".join(
        f"{report.count(outcome)} {label}"
        for outcome, label in _OUTCOME_LABELS.items()
        if report.count(outcome)
    )
    typer.echo(f"\n{len(report.outcomes)} {unit}: {summary or 'nothing to do'}")
    if report.count("error"):
        raise typer.Exit(1)


@app.command("build-contractors")
def build_contractors_command(
    ctx: typer.Context,
    workbook: Annotated[Path, typer.Argument(help="The contractors master workbook (.xlsx).")],
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Output CSV (default: contractors.csv next to the workbook)."),
    ] = None,
) -> None:
    """Build contractors.csv from the master workbook: confirmed contractors only, tabs
    joined on Talent ID. An existing output file is first copied to a backups/ folder
    next to it. Strengths the skill taxonomy does not know yet are learned into
    paths.learned_skills, with merge suggestions in skill_suggestions.csv next to the list."""
    import shutil
    from datetime import UTC, datetime

    from jobmatch.sources.contractor_sheet import (
        ContractorRow,
        build_contractors,
        default_output,
        load_contractor_import,
    )
    from jobmatch.sources.xlsx import WorkbookError, read_sheets
    from jobmatch.storage.csv_store import export_csv

    settings: Settings = ctx.obj
    try:
        rows, report = build_contractors(
            read_sheets(workbook), load_contractor_import(settings.config_dir)
        )
    except (WorkbookError, ValueError) as exc:
        typer.echo(f"Cannot build contractors: {exc}")
        raise typer.Exit(1) from exc
    dest = out or default_output(workbook)
    if dest.exists():
        backups = dest.parent / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = backups / f"{dest.stem}.{stamp}{dest.suffix}"
        shutil.copy2(dest, backup)
        typer.echo(f"Previous file copied to {backup}")
    export_csv(ContractorRow, rows, dest)
    typer.echo(f"Wrote {report.exported} contractor(s) to {dest}")
    typer.echo(
        f"  with a profile: {report.with_profile} · without a profile: {report.without_profile}"
    )
    typer.echo(
        f"  excluded: {report.not_contract} not confirmed as contract · "
        f"{report.no_profile_text} with no summary or core strengths · "
        f"{report.profile_without_talent} profile(s) with no Talent row"
    )
    typer.echo(
        f"  rate: {report.rates_numeric} hourly figure(s) · "
        f"{report.rates_default} set to the default"
    )
    log.info("build_contractors", extra={"fields": report.model_dump()})
    _learn_contractor_skills(settings, rows, dest.parent)


def _learn_contractor_skills(
    settings: Settings, rows: "list[ContractorProfile]", folder: Path
) -> None:
    """Learned skills and merge suggestions go next to the contractor list just built, so a
    build to another folder (--out) never replaces the live paths.learned_skills."""
    from jobmatch.normalize.skill_learning import (
        SkillSuggestion,
        learn_skills,
        load_skill_learning,
        write_learned,
    )
    from jobmatch.storage.csv_store import export_csv

    learned, suggestions, report = learn_skills(
        rows, load_skills_taxonomy(settings.config_dir), load_skill_learning(settings.config_dir)
    )
    learned_path = folder / settings.paths.learned_skills.name
    suggestions_path = folder / "skill_suggestions.csv"
    write_learned(learned, learned_path)
    export_csv(SkillSuggestion, suggestions, suggestions_path)
    typer.echo(
        f"Skills: {report.phrases} strength phrase(s) · {report.known} already known · "
        f"{report.learned} learned · {report.skipped} too short or generic"
    )
    typer.echo(f"  learned skills: {learned_path}")
    typer.echo(f"  merge suggestions: {suggestions_path}")
    if report.learned:
        typer.echo(
            "  New skills tag jobs only after they are re-normalized: run "
            "bash scripts/reset_reimport.sh, then embed-contractors, embed-jobs and match."
        )
    log.info("learn_contractor_skills", extra={"fields": report.model_dump()})


def _print_embed_report(report: "EmbedReport") -> None:
    typer.echo(
        f"{report.namespace}: {report.total} vector(s) · {report.embedded} embedded · "
        f"{report.reused} unchanged · {report.dropped} dropped"
    )
    per_text = ""
    if report.embedded:
        per_text = f" ({1000 * report.seconds / report.embedded:.0f} ms/text)"
    typer.echo(
        f"  model {report.model_name} ({report.model_version}) · {report.dim} dimensions · "
        f"{report.seconds} s{per_text}"
    )


@app.command("embed-contractors")
def embed_contractors(
    ctx: typer.Context,
    contractors_csv: Annotated[
        Path | None,
        typer.Option("--contractors", help="Contractor list (default: paths.contractors_csv)."),
    ] = None,
) -> None:
    """Embed every contractor in the contractor list, plus each of their titles (only
    changed ones are re-embedded). The first run downloads the embedding model."""
    from jobmatch.config import load_normalization_rules, load_skills_taxonomy
    from jobmatch.embed.build import TextItem, embed_items
    from jobmatch.embed.encoder import Encoder
    from jobmatch.embed.texts import (
        TEMPLATE_HASH,
        TITLE_TEMPLATE_HASH,
        TitleCleaner,
        contractor_skills,
        contractor_text,
        contractor_title_items,
    )
    from jobmatch.normalize.skills import SkillExtractor
    from jobmatch.storage.csv_store import CsvContractorProfileRepository

    settings: Settings = ctx.obj
    path = contractors_csv or settings.paths.contractors_csv
    if not path.exists():
        typer.echo(f"Contractor list not found: {path}. Run build-contractors first.")
        raise typer.Exit(1)
    profiles = CsvContractorProfileRepository(
        path, lock_timeout=settings.storage.lock_timeout_seconds
    ).list_all()
    extractor = SkillExtractor(
        load_skills_taxonomy(settings.config_dir, settings.paths.learned_skills)
    )
    rules = load_normalization_rules(settings.config_dir)
    cleaner = TitleCleaner(rules.seniority, rules.title_engagement_words)
    encoder = Encoder(settings.embedding)  # one model load for both vector sets
    store = _vector_store(settings)
    for namespace, items, template in (
        (
            "contractors",
            [
                TextItem(id=p.talent_id, text=contractor_text(p, contractor_skills(p, extractor)))
                for p in profiles
            ],
            TEMPLATE_HASH,
        ),
        (
            "contractor_titles",
            [
                TextItem(id=item_id, text=text)
                for p in profiles
                for item_id, text in contractor_title_items(p, cleaner)
            ],
            TITLE_TEMPLATE_HASH,
        ),
    ):
        report = embed_items(
            namespace, items, kind="facet", encoder=encoder, store=store, template_hash=template
        )
        _print_embed_report(report)
        log.info("embed_contractors", extra={"fields": report.model_dump()})


@app.command("embed-jobs")
def embed_jobs(ctx: typer.Context) -> None:
    """Embed active jobs in jobs.csv (new, matched, no_match), plus each job title, each
    job's core (title and first responsibilities) and the profession descriptions in
    config/professions.yaml (for profession notes); filtered-out jobs and stubs without text
    are skipped. Only changed texts are re-embedded."""
    from jobmatch.config import load_normalization_rules, load_professions
    from jobmatch.embed.build import TextItem, embed_items
    from jobmatch.embed.encoder import Encoder, TextKind
    from jobmatch.embed.texts import (
        PROFESSION_TEMPLATE_HASH,
        TEMPLATE_HASH,
        TITLE_TEMPLATE_HASH,
        TitleCleaner,
        job_core_text,
        job_text,
        job_title_text,
        profession_text,
    )
    from jobmatch.schemas import JobStatus
    from jobmatch.storage.csv_store import CsvJobRepository

    settings: Settings = ctx.obj
    store = CsvStore(settings.paths.data_dir, lock_timeout=settings.storage.lock_timeout_seconds)
    active = {JobStatus.NEW, JobStatus.MATCHED, JobStatus.NO_MATCH}
    jobs = [j for j in CsvJobRepository(store).list_all() if j.status in active]
    rules = load_normalization_rules(settings.config_dir)
    cleaner = TitleCleaner(rules.seniority, rules.title_engagement_words)
    titles = [(j.job_id, job_title_text(j, cleaner)) for j in jobs]
    encoder = Encoder(settings.embedding)  # one model load for every vector set
    vectors = _vector_store(settings)
    sets: list[tuple[str, list[TextItem], str, TextKind]] = [
        ("jobs", [TextItem(id=j.job_id, text=job_text(j)) for j in jobs], TEMPLATE_HASH, "job"),
        (
            "job_titles",
            [TextItem(id=job_id, text=text) for job_id, text in titles if text],
            TITLE_TEMPLATE_HASH,
            "job",
        ),
    ]
    professions = load_professions(settings.config_dir)
    if professions is not None:
        sets += [
            (
                "job_cores",
                [TextItem(id=j.job_id, text=job_core_text(j)) for j in jobs],
                PROFESSION_TEMPLATE_HASH,
                "job",
            ),
            (
                "professions",
                [
                    TextItem(id=name, text=profession_text(name, description))
                    for name, description in professions.professions.items()
                ],
                PROFESSION_TEMPLATE_HASH,
                "facet",
            ),
        ]
    for namespace, items, template, kind in sets:
        report = embed_items(
            namespace, items, kind=kind, encoder=encoder, store=vectors, template_hash=template
        )
        _print_embed_report(report)
        log.info("embed_jobs", extra={"fields": report.model_dump()})


def _vector_store(settings: Settings) -> NpzVectorStore:
    return NpzVectorStore(
        settings.paths.data_dir / "embeddings", lock_timeout=settings.storage.lock_timeout_seconds
    )


@app.command("match")
def match(
    ctx: typer.Context,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Score and print, but store nothing.")
    ] = False,
) -> None:
    """Shortlist contractors for every active job from the stored vectors. Matches that pass
    every rule check go to matches.csv, the others to matches_flagged.csv with the reason."""
    from jobmatch.match.professions import profession_notes
    from jobmatch.match.run import ACTIVE_STATUSES, MatchError, run_matching
    from jobmatch.storage.csv_store import CsvJobRepository

    settings: Settings = ctx.obj
    try:
        report, results, profiles = run_matching(settings, dry_run=dry_run)
    except MatchError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    store = CsvStore(settings.paths.data_dir, lock_timeout=settings.storage.lock_timeout_seconds)
    active = [j for j in CsvJobRepository(store).list_all() if j.status in ACTIVE_STATUSES]
    notes, no_notes = profession_notes(settings, active, list(profiles.values()))

    for result in results:
        typer.echo(f"\n{result.title}  [{result.job_id}]")
        if not result.candidates:
            typer.echo("  no contractor above min_score")
        for c in result.candidates:
            # Talent ID and headline only: names stay out of the terminal and logs.
            headline = profiles[c.contractor_id].standardized_headline or ""
            mark = "FLAGGED" if c.flagged else c.band.value
            typer.echo(f"  {c.rank}. {c.score:.3f} {mark:<7} {c.contractor_id}  {headline}")
            parts = [f"text {c.cosine:.3f}"]
            if c.title_cosine is not None:
                parts.append(f"title {c.title_cosine:.3f} ({c.matched_title})")
            if c.skill_overlap is not None:
                parts.append(f"skills {c.skill_overlap:.2f}")
            if c.lexical is not None:
                parts.append(f"words {c.lexical:.3f}")
            typer.echo(f"       {' · '.join(parts)}")
            for reason in c.gates_failed:
                typer.echo(f"       - {reason}")
            if c.missing_must_have:
                typer.echo(f"       - missing must-have: {', '.join(c.missing_must_have)}")
            if note := notes.get((result.job_id, c.contractor_id)):
                typer.echo(f"       - profession (hint): {note.text()}")
    prefix = "[dry run] " if report.dry_run else ""
    typer.echo(
        f"\n{prefix}{report.jobs} job(s) × {report.contractors} contractor(s) · "
        f"{report.matched} matched · {report.no_match} no match · {report.seconds} s"
    )
    if not report.dry_run:
        typer.echo(
            f"stored {report.stored} in matches.csv · {report.flagged} in matches_flagged.csv · "
            f"{report.unchanged} unchanged · {report.status_changes} job status change(s)"
        )
    typer.echo(f"scoring version {report.scoring_version}")
    if no_notes:
        typer.echo(f"Profession notes left out: {no_notes}")
    log.info("match", extra={"fields": report.model_dump()})


@app.command("export-matches")
def export_matches(ctx: typer.Context) -> None:
    """Write the current matches (shortlist and flagged) to exports/matches-<time>.csv for
    Excel. The file holds contractor names: keep it local."""
    from datetime import UTC, datetime

    from jobmatch.match.export import build_rows, write_export
    from jobmatch.match.run import ACTIVE_STATUSES
    from jobmatch.storage.csv_store import (
        CsvContractorProfileRepository,
        CsvJobRepository,
        CsvMatchRepository,
    )

    settings: Settings = ctx.obj
    timeout = settings.storage.lock_timeout_seconds
    store = CsvStore(settings.paths.data_dir, lock_timeout=timeout)
    path = settings.paths.contractors_csv
    profiles = (
        CsvContractorProfileRepository(path, lock_timeout=timeout).list_all()
        if path.exists()
        else []
    )
    jobs = {j.job_id: j for j in CsvJobRepository(store).list_all()}
    rows = build_rows(
        CsvMatchRepository(store).list_all(),
        CsvMatchRepository(store, flagged=True).list_all(),
        jobs,
        {p.talent_id: p for p in profiles},
        _profession_notes(
            settings, [j for j in jobs.values() if j.status in ACTIVE_STATUSES], profiles
        ),
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = settings.paths.exports_dir / f"matches-{stamp}.csv"
    count = write_export(rows, dest)
    typer.echo(f"Wrote {count} match row(s) to {dest}")
    log.info("export_matches", extra={"fields": {"rows": count}})


@app.command("export-trial-run")
def export_trial_run(ctx: typer.Context) -> None:
    """Write the best shortlisted contractors per active job (exports.trial_run_per_job) to
    exports/trial-run-<time>.csv, read by data_visualizer.ipynb. A job with no shortlist
    gets a "no match" row. The file holds contractor names: keep it local."""
    from datetime import UTC, datetime

    from jobmatch.match.export import build_trial_rows, write_trial_run
    from jobmatch.match.run import ACTIVE_STATUSES
    from jobmatch.storage.csv_store import (
        CsvContractorProfileRepository,
        CsvJobRepository,
        CsvMatchRepository,
    )

    settings: Settings = ctx.obj
    timeout = settings.storage.lock_timeout_seconds
    store = CsvStore(settings.paths.data_dir, lock_timeout=timeout)
    path = settings.paths.contractors_csv
    profiles = (
        CsvContractorProfileRepository(path, lock_timeout=timeout).list_all()
        if path.exists()
        else []
    )
    active = [j for j in CsvJobRepository(store).list_all() if j.status in ACTIVE_STATUSES]
    rows = build_trial_rows(
        CsvMatchRepository(store).list_all(),
        active,
        {p.talent_id: p for p in profiles},
        settings.exports.trial_run_per_job,
        CsvMatchRepository(store, flagged=True).list_all(),
        _profession_notes(settings, active, profiles),
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = settings.paths.exports_dir / f"trial-run-{stamp}.csv"
    count = write_trial_run(rows, dest)
    jobs = len({r.job_url for r in rows})
    typer.echo(f"Wrote {count} row(s) for {jobs} job(s) to {dest}")
    log.info("export_trial_run", extra={"fields": {"rows": count, "jobs": jobs}})


def _profession_notes(
    settings: Settings, jobs: list["Job"], profiles: list["ContractorProfile"]
) -> dict[tuple[str, str], str]:
    """Profession notes for the exports, or none (with the reason) when vectors are missing."""
    from jobmatch.match.professions import profession_notes

    notes, reason = profession_notes(settings, jobs, profiles)
    if reason:
        typer.echo(f"Profession notes left out: {reason}")
    return {pair: note.text() for pair, note in notes.items()}


def _reviewed_table(settings: Settings, path: Path | None = None) -> "CsvTable[ReviewedMatch]":
    from jobmatch.schemas import ReviewedMatch
    from jobmatch.storage.csv_store import CsvTable, TableMode

    path = path or settings.paths.data_dir / "eval" / "reviewed_matches.csv"
    return CsvTable(
        path,
        ReviewedMatch,
        TableMode.READ_WRITE,
        backups_dir=path.parent / "backups",
        lock_timeout=settings.storage.lock_timeout_seconds,
        unique_key=("match_id",),
    )


@app.command("import-reviewed-matches")
def import_reviewed_matches(
    ctx: typer.Context,
    sheet: Annotated[Path, typer.Argument(help="Reviewed-match sheet exported as CSV.")],
) -> None:
    """Import human-reviewed matches (contract-type engagements only) into
    data/eval/reviewed_matches.csv, the labels evaluate-matches compares against. Contact,
    outreach and talent-name columns are not imported. An existing file is backed up and
    replaced."""
    from jobmatch.sources.reviewed_matches import (
        SheetError,
        load_reviewed_import,
        read_reviewed_sheet,
    )

    settings: Settings = ctx.obj
    try:
        rows, report = read_reviewed_sheet(sheet, load_reviewed_import(settings.config_dir))
    except (OSError, SheetError) as exc:
        typer.echo(f"Cannot read the sheet: {exc}")
        raise typer.Exit(1) from exc
    table = _reviewed_table(settings)
    table.path.parent.mkdir(parents=True, exist_ok=True)
    table.rewrite(rows)
    typer.echo(f"Wrote {report.kept} reviewed match(es) to {table.path}")
    typer.echo(
        f"  {report.rows} filled-in row(s) · left out: {report.permanent} permanent · "
        f"{report.no_engagement} without an engagement type"
    )
    log.info("import_reviewed_matches", extra={"fields": report.model_dump()})


@app.command("evaluate-matches")
def evaluate_matches(ctx: typer.Context) -> None:
    """Compare the matcher's scores with the human reviews in data/eval/reviewed_matches.csv
    and write the pair-by-pair comparison to exports/evaluation-<time>.csv. Read-only."""
    from collections import Counter
    from datetime import UTC, datetime

    from jobmatch.match.evaluate import EvalPair, evaluate
    from jobmatch.match.run import MatchError, score_all_jobs
    from jobmatch.storage.csv_store import export_csv

    settings: Settings = ctx.obj
    table = _reviewed_table(settings)
    if not table.path.exists():
        typer.echo(f"No reviews found at {table.path}. Run import-reviewed-matches first.")
        raise typer.Exit(1)
    try:
        scored = score_all_jobs(settings)
    except MatchError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    report = evaluate(table.read_all(), scored, settings.matching)

    skipped = Counter(p.status for p in report.pairs if p.status != "compared")
    typer.echo(f"{len(report.pairs)} reviewed pair(s) · {report.compared} compared")
    for reason, n in skipped.most_common():
        typer.echo(f"  not compared: {n} · {reason}")
    typer.echo(
        f"\nRank correlation of our score with the reviewers' total: {report.spearman_total}"
        f" · with their skills score: {report.spearman_skills}"
    )
    typer.echo("\nReviewer tier -> our result")
    for tier, outcomes in report.tier_band.items():
        detail = " · ".join(f"{k} {v}" for k, v in sorted(outcomes.items()))
        typer.echo(f"  {tier:<7} {detail}")
    typer.echo("\nPer job: reviewers' A/B talents in our shortlist")
    for r in report.recall:
        typer.echo(
            f"  {r.good_shortlisted}/{r.good} A/B · {r.other_shortlisted} C/reject also shortlisted"
            f"  {r.role or ''}  [{r.job_id}]"
        )
    typer.echo("\nPairs (talent IDs only)")
    for p in sorted(
        (p for p in report.pairs if p.status == "compared"),
        key=lambda p: (p.job_id or "", p.our_rank or 0),
    ):
        mark = "FLAGGED" if p.flagged else (p.our_band or "")
        where = "shortlisted" if p.shortlisted else "not shortlisted"
        typer.echo(
            f"  {p.match_id} {p.talent_id}: reviewers {p.human_tier} {p.human_score} "
            f"(skills {p.human_skills}) · ours {p.our_score:.3f} {mark}, rank "
            f"{p.our_rank}/{p.contractors}, {where}  {p.role or ''}"
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = settings.paths.exports_dir / f"evaluation-{stamp}.csv"
    export_csv(EvalPair, report.pairs, dest)
    typer.echo(f"\nWrote {len(report.pairs)} pair(s) to {dest}")
    log.info(
        "evaluate_matches",
        extra={
            "fields": {
                "pairs": len(report.pairs),
                "compared": report.compared,
                "spearman_total": report.spearman_total,
                "spearman_skills": report.spearman_skills,
            }
        },
    )


# ---- v1: Gemini matching (doc/V1_PLAN.md) ----


def _llm_inputs(
    settings: Settings,
) -> "tuple[LlmSettings, CsvStore, list[Job], list[ContractorProfile], Roster]":
    """The llm settings, active jobs, contractor list and anonymized roster, or exit 1 with
    the reason. Prints counts only (talent IDs, never names)."""
    from jobmatch.config import load_professions
    from jobmatch.llm.anonymize import build_roster
    from jobmatch.llm.payload import ACTIVE_STATUSES
    from jobmatch.llm.preview import profession_problems
    from jobmatch.storage.csv_store import (
        CsvContractorProfileRepository,
        CsvJobRepository,
        CsvStoreError,
    )

    llm = settings.llm
    if llm is None:
        typer.echo("settings.yaml has no llm section.")
        raise typer.Exit(1)
    contractors_csv = settings.paths.contractors_csv
    if not contractors_csv.exists():
        typer.echo(f"No contractor list at {contractors_csv}. Run build-contractors first.")
        raise typer.Exit(1)
    store = CsvStore(settings.paths.data_dir, lock_timeout=settings.storage.lock_timeout_seconds)
    try:
        jobs = [j for j in CsvJobRepository(store).list_all() if j.status in ACTIVE_STATUSES]
        profiles = CsvContractorProfileRepository(
            contractors_csv, lock_timeout=settings.storage.lock_timeout_seconds
        ).list_all()
    except CsvStoreError as exc:
        typer.echo(f"Cannot read data: {exc}")
        raise typer.Exit(1) from exc

    roster = build_roster(profiles, llm.payload)
    typer.echo(f"{len(jobs)} active job(s) · {len(roster.cards)} contractor card(s)")
    for ex in roster.excluded:
        typer.echo(f"  left out: {ex.talent_id} ({ex.reason})")
    professions = load_professions(settings.config_dir)
    missing, unknown = profession_problems(profiles, professions.professions if professions else [])
    if missing:
        typer.echo(f"  {missing} contractor(s) have no primary profession yet (prefilter input)")
    if unknown:
        typer.echo(f"  profession not in professions.yaml: {', '.join(unknown)}")
    return llm, store, jobs, profiles, roster


def _gemini(llm: "LlmSettings") -> "GeminiProvider":
    """The Gemini client, or exit 1 when the key is missing (nothing is sent)."""
    from jobmatch.llm.gemini import GeminiProvider
    from jobmatch.llm.provider import LlmConfigError

    try:
        return GeminiProvider.from_settings(llm)
    except LlmConfigError as exc:
        typer.echo(f"{exc}. Nothing was sent.")
        raise typer.Exit(1) from exc


@app.command("llm-preview")
def llm_preview(
    ctx: typer.Context,
    job_ids: Annotated[
        list[str] | None,
        typer.Option("--job-id", help="Job to preview (repeatable). Default: the first --limit."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, help="Jobs to preview.")] = 3,
    stats: Annotated[
        bool,
        typer.Option("--stats", help="Token estimates for every active job; writes nothing."),
    ] = False,
) -> None:
    """Build the Gemini requests a run would send, WITHOUT sending anything. Writes each
    request body to data/llm_preview/<time>/ so a person can check what would leave the
    machine (contractors appear only as anonymized cards). --stats prints token estimates
    and how many requests and days a full run needs at the limits in settings.yaml."""
    from jobmatch.llm.preview import (
        preview_calls,
        preview_folder,
        preview_stats,
        write_preview,
    )
    from jobmatch.llm.usage import UsageLedger, check_run

    settings: Settings = ctx.obj
    llm, store, jobs, profiles, roster = _llm_inputs(settings)

    if stats:
        s = preview_stats(jobs, roster, llm)
        typer.echo(
            f"\nModel {llm.model} · limits {llm.limits.rpm} RPM, {llm.limits.tpm:,} TPM, "
            f"{llm.limits.rpd} RPD (from settings.yaml; estimates use "
            f"{llm.payload.chars_per_token:g} characters per token)"
        )
        typer.echo(f"Roster: about {s.roster_tokens:,} tokens for {s.contractors} contractor(s)")
        typer.echo(
            f"Job text: median {s.job_tokens.median:,} · p90 {s.job_tokens.p90:,} · "
            f"max {s.job_tokens.max:,} tokens"
        )
        typer.echo(
            f"Per call input: median {s.call_input_tokens.median:,} · "
            f"p90 {s.call_input_tokens.p90:,} · max {s.call_input_tokens.max:,} tokens "
            f"(largest: {s.largest_call_job}), plus about {s.output_tokens_per_call:,} output"
        )
        typer.echo(
            f"Full run: {s.calls} call(s) · about {s.total_tokens:,} tokens · "
            f"{s.days_at_rpd} day(s) of requests at {llm.limits.rpd} RPD"
        )
        tokens_per_call = s.call_input_tokens.median + s.output_tokens_per_call
        check = check_run(
            llm.limits, llm.budget, UsageLedger(store).today(), s.calls, tokens_per_call
        )
        typer.echo(f"Budget today: {check.verdict.upper()} · {check.message}")
        log.info("llm_preview_stats", extra={"fields": s.model_dump()})
        return

    if job_ids:
        by_id = {j.job_id: j for j in jobs}
        absent = [i for i in job_ids if i not in by_id]
        if absent:
            typer.echo(f"Not an active job: {', '.join(absent)}")
            raise typer.Exit(1)
        selected = [by_id[i] for i in job_ids]
    else:
        selected = jobs[:limit]
    if not selected:
        typer.echo("No active jobs to preview.")
        raise typer.Exit(1)
    calls = preview_calls(selected, roster, llm)
    folder = preview_folder(settings.paths.data_dir)
    write_preview(calls, llm, folder)
    for call in calls:
        typer.echo(
            f"  {call.job_ids[0]}: about {call.estimated_input_tokens:,} input tokens "
            f"(+{llm.payload.expected_output_tokens:,} output expected)"
        )
    typer.echo(f"Wrote {len(calls)} request(s) and aliases.json to {folder}")
    typer.echo("Nothing was sent. Read the files before approving any API call.")
    log.info("llm_preview", extra={"fields": {"calls": len(calls), "folder": str(folder)}})


@app.command("suggest-professions")
def suggest_professions(ctx: typer.Context) -> None:
    """ONE Gemini call: suggest a primary profession (and up to 2 secondary ones) from
    config/professions.yaml for every contractor, from the anonymized cards. Writes
    data/contractors/profession_suggestions-<time>.csv for a person to check; apply it with
    apply-professions. Prints the call's token usage."""
    from datetime import UTC, datetime

    from jobmatch.config import load_professions
    from jobmatch.llm.professions import parse_answer, prepare_profession_call, write_suggestions
    from jobmatch.llm.runner import send
    from jobmatch.llm.usage import UsageLedger, check_run

    settings: Settings = ctx.obj
    llm, store, _, _, roster = _llm_inputs(settings)
    professions = load_professions(settings.config_dir)
    if professions is None or not roster.cards:
        typer.echo("Nothing to ask: professions.yaml or contractor cards are missing.")
        raise typer.Exit(1)
    call = prepare_profession_call(roster, professions, llm)
    ledger = UsageLedger(store)
    check = check_run(llm.limits, llm.budget, ledger.today(), 1, call.estimated_total_tokens)
    typer.echo(f"Budget: {check.verdict.upper()} · {check.message}")
    if check.verdict == "exceeds":
        raise typer.Exit(1)
    outcome = send(
        _gemini(llm), ledger, llm, call.request,
        purpose="classify_contractors", estimated_tokens=call.estimated_total_tokens,
    )  # fmt: skip
    typer.echo(outcome.line)
    log.info("llm_call", extra={"fields": outcome.usage.model_dump(mode="json")})
    if outcome.response is None:
        typer.echo(f"Call failed: {outcome.usage.note}")
        raise typer.Exit(1)
    now = datetime.now(UTC)
    try:
        parsed = parse_answer(
            outcome.response.text, roster, professions, model=outcome.response.model, now=now
        )
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    for problem in parsed.problems:
        typer.echo(f"  problem: {problem}")
    dest = settings.paths.contractors_csv.parent / (
        f"profession_suggestions-{now.strftime('%Y%m%dT%H%M%SZ')}.csv"
    )
    write_suggestions(parsed.suggestions, dest)
    for s in parsed.suggestions:
        extra = f" (+ {', '.join(s.secondary_professions)})" if s.secondary_professions else ""
        typer.echo(f"  {s.talent_id}: {s.primary_profession}{extra} [{s.confidence}]")
    typer.echo(f"Wrote {len(parsed.suggestions)} suggestion(s) to {dest}")
    typer.echo("Check them, then: apply-professions <that file>")


@app.command("apply-professions")
def apply_professions(
    ctx: typer.Context,
    suggestions: Annotated[Path, typer.Argument(help="A profession_suggestions-*.csv file.")],
) -> None:
    """Write suggested professions into contractors.csv (primary and secondary professions
    only), after a timestamped backup in data/contractors/backups/. No API call.
    build-contractors rebuilds contractors.csv from the workbook, so put the same values in
    the workbook's profession columns to keep them."""
    from jobmatch.llm.professions import apply_suggestions, read_suggestions
    from jobmatch.storage.csv_store import CsvContractorProfileRepository, CsvStoreError

    settings: Settings = ctx.obj
    dest = settings.paths.contractors_csv
    try:
        rows = read_suggestions(suggestions)
        profiles = CsvContractorProfileRepository(
            dest, lock_timeout=settings.storage.lock_timeout_seconds
        ).list_all()
    except (OSError, ValueError, CsvStoreError) as exc:
        typer.echo(f"Cannot read: {exc}")
        raise typer.Exit(1) from exc
    report = apply_suggestions(profiles, rows, dest)
    if report.backup:
        typer.echo(f"Previous file copied to {report.backup}")
    typer.echo(f"Updated {report.updated} contractor(s) · {report.unchanged} unchanged · {dest}")
    if report.not_in_list:
        typer.echo(f"  not in the contractor list: {', '.join(report.not_in_list)}")
    typer.echo("Reminder: add the same values to the workbook, or build-contractors drops them.")
    log.info("apply_professions", extra={"fields": report.model_dump(mode="json")})


@app.command("llm-try")
def llm_try(
    ctx: typer.Context,
    job_id: Annotated[
        str | None, typer.Option("--job-id", help="Job to judge. Default: the first active job.")
    ] = None,
) -> None:
    """Phase 4: judge ONE job with ONE real Gemini call (the whole anonymized roster).
    Prints the token usage, the verdicts (talent IDs and headlines only) and any problem with
    the answer, and saves everything to data/llm_results/<time>/. Changes no match data."""
    from datetime import UTC, datetime

    from jobmatch.llm.payload import job_card, render_job
    from jobmatch.llm.preview import safe_name
    from jobmatch.llm.prompt import prepare_call
    from jobmatch.llm.runner import send
    from jobmatch.llm.trial import TrialResult, judge_answer, write_result
    from jobmatch.llm.usage import UsageLedger, check_run

    settings: Settings = ctx.obj
    llm, store, jobs, profiles, roster = _llm_inputs(settings)
    if job_id is None:
        if not jobs:
            typer.echo("No active jobs.")
            raise typer.Exit(1)
        job = jobs[0]
    else:
        found = [j for j in jobs if j.job_id == job_id]
        if not found:
            typer.echo(f"Not an active job: {job_id}")
            raise typer.Exit(1)
        job = found[0]
    card = job_card(job, llm.payload)
    call = prepare_call(card, roster, llm)
    ledger = UsageLedger(store)
    check = check_run(llm.limits, llm.budget, ledger.today(), 1, call.estimated_total_tokens)
    typer.echo(f"Budget: {check.verdict.upper()} · {check.message}")
    if check.verdict == "exceeds":
        raise typer.Exit(1)
    outcome = send(
        _gemini(llm), ledger, llm, call.request,
        purpose="judge_try", job_ids=call.job_ids, estimated_tokens=call.estimated_total_tokens,
    )  # fmt: skip
    typer.echo(outcome.line)
    log.info("llm_call", extra={"fields": outcome.usage.model_dump(mode="json")})

    judged = None
    if outcome.response is not None:
        judged = judge_answer(outcome.response.text, render_job(card), roster, llm.top_k)
    result = TrialResult(
        job_id=job.job_id,
        model=outcome.response.model if outcome.response else llm.model,
        judge_key=call.judge_key(llm.model),
        usage=outcome.usage,
        aliases=roster.aliases,
        judged=judged,
        raw_text=outcome.response.text if outcome.response else None,
    )
    folder = settings.paths.data_dir / "llm_results" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = write_result(result, folder, safe_name(job.job_id))

    typer.echo(f"\n{card.title}  [{job.job_id}]")
    if outcome.response is None:
        typer.echo(f"Call failed: {outcome.usage.note}")
    elif judged is None or judged.output is None:
        typer.echo(f"Answer not usable: {judged.error if judged else 'empty'}")
    else:
        headline = {p.talent_id: p.standardized_headline or "" for p in profiles}
        out = judged.output
        counts = {
            v: sum(1 for x in out.verdicts if x.verdict == v) for v in ("fit", "partial", "no")
        }
        typer.echo(
            f"Verdicts: {counts['fit']} fit · {counts['partial']} partial · {counts['no']} no"
            + (" · no_fit" if out.no_fit else "")
        )
        for rank, m in enumerate(out.top, start=1):
            tid = roster.aliases.get(m.alias, m.alias)
            verdict = next((v.verdict for v in out.verdicts if v.alias == m.alias), "?")
            typer.echo(f"  {rank}. {m.fit_score:>3} {verdict:<7} {tid}  {headline.get(tid, '')}")
            if m.requirements_missing:
                typer.echo(f"       missing: {'; '.join(m.requirements_missing)}")
            for flag in m.red_flags:
                typer.echo(f"       flag ({flag.kind}): {flag.detail}")
            typer.echo(f"       evidence kept: {len(m.evidence)}")
        for problem in judged.problems:
            typer.echo(f"  problem: {problem}")
        for alias, quotes in judged.unverified.items():
            tid = roster.aliases.get(alias, alias)
            typer.echo(f"  unverified evidence removed for {tid}: {len(quotes)} quote(s)")
    typer.echo(f"Saved to {path}")
    if outcome.response is None or judged is None or judged.output is None:
        raise typer.Exit(1)


def _default_labels(settings: Settings) -> Path:
    return settings.paths.data_dir / "eval" / "blind_consensus.csv"


@app.command("llm-judge")
def llm_judge(
    ctx: typer.Context,
    labelled: Annotated[
        bool, typer.Option("--labelled", help="Only jobs that have blind labels (phase 5).")
    ] = False,
    labels: Annotated[
        Path | None,
        typer.Option("--labels", help="Label file (default data/eval/blind_consensus.csv)."),
    ] = None,
    job_ids: Annotated[list[str] | None, typer.Option("--job-id", help="Repeatable.")] = None,
    exclude_language: Annotated[
        list[str] | None,
        typer.Option("--exclude-language", help="Skip postings in this language (en, fr…)."),
    ] = None,
    max_calls: Annotated[
        int | None, typer.Option("--max-calls", min=1, help="Stop after this many calls.")
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Plan and estimate only.")] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Go ahead even if the run would use most of today's quota."),
    ] = False,
) -> None:
    """Judge active jobs with Gemini, one call per job, paced and logged. Answers are cached in
    data/llm_results/judgments/ (a re-run only pays for jobs whose text, roster, model or
    prompt changed). Changes no job or match file."""
    import statistics

    from jobmatch.llm.compare import read_labels
    from jobmatch.llm.judge import judge_jobs, judgments_dir, plan_jobs
    from jobmatch.llm.usage import UsageLedger, check_run

    settings: Settings = ctx.obj
    llm, store, jobs, _, roster = _llm_inputs(settings)
    if labelled:
        path = labels or _default_labels(settings)
        wanted = {p.job_id for p in read_labels(path)}
        absent = wanted - {j.job_id for j in jobs}
        jobs = [j for j in jobs if j.job_id in wanted]
        typer.echo(
            f"Labelled jobs: {len(wanted)} · active: {len(jobs)} · not active: {len(absent)}"
        )
    if job_ids:
        jobs = [j for j in jobs if j.job_id in set(job_ids)]
    if exclude_language:
        skip = set(exclude_language)
        before = len(jobs)
        jobs = [j for j in jobs if not (j.language and j.language.value in skip)]
        typer.echo(f"Left out {before - len(jobs)} job(s) in {', '.join(sorted(skip))}")
    if not jobs:
        typer.echo("No jobs to judge.")
        raise typer.Exit(1)
    folder = judgments_dir(settings.paths.data_dir)
    plan = plan_jobs(jobs, roster, llm, folder)
    todo = [p for p in plan if not p.cached]
    calls = min(len(todo), max_calls) if max_calls else len(todo)
    per_call = int(statistics.median(p.call.estimated_total_tokens for p in plan))
    ledger = UsageLedger(store)
    check = check_run(llm.limits, llm.budget, ledger.today(), calls, per_call)
    typer.echo(
        f"{len(plan)} job(s): {len(plan) - len(todo)} already judged (cached) · {calls} call(s) "
        f"to make · about {per_call:,} tokens each, {calls * per_call:,} in all"
    )
    typer.echo(f"Budget: {check.verdict.upper()} · {check.message}")
    if dry_run:
        typer.echo("Dry run: nothing was sent.")
        return
    if check.verdict == "exceeds":
        typer.echo(f"Stopped. Use --max-calls {check.calls_that_fit} to run what fits today.")
        raise typer.Exit(1)
    if check.verdict == "warn" and not yes:
        typer.echo("Stopped. Add --yes to go ahead, or --max-calls to run fewer.")
        raise typer.Exit(1)
    report = judge_jobs(
        jobs, roster, llm, _gemini(llm), ledger, folder, max_calls=max_calls, echo=typer.echo
    )
    typer.echo(
        f"\n{report.sent} call(s) sent · {report.ok} usable answer(s) · {report.failed} failed · "
        f"{report.cached} cached · tokens in {report.input_tokens:,} · out "
        f"{report.output_tokens:,} · total {report.total_tokens:,}"
    )
    today = ledger.today()
    typer.echo(f"Today: {today.requests}/{llm.limits.rpd} requests · {today.tokens:,} tokens")
    if report.stopped:
        typer.echo(f"Stopped early: {report.stopped}")
    log.info("llm_judge", extra={"fields": report.model_dump()})


@app.command("llm-compare")
def llm_compare(
    ctx: typer.Context,
    labels: Annotated[
        Path | None,
        typer.Option("--labels", help="Label file (default data/eval/blind_consensus.csv)."),
    ] = None,
) -> None:
    """Compare Gemini's cached verdicts (current prompt version) with the blind labels and with
    v0's latest stored run (matches.csv + matches_flagged.csv, newest scoring version; v0 is
    not re-run). No API call. Writes the pair-by-pair table to data/llm_results/."""
    import json
    from datetime import UTC, datetime

    from jobmatch.llm.compare import compare, latest_v0, read_labels
    from jobmatch.llm.judge import judgments_dir
    from jobmatch.llm.prompt import PROMPT_VERSION
    from jobmatch.llm.trial import TrialResult
    from jobmatch.storage.csv_store import CsvStoreError, export_csv

    settings: Settings = ctx.obj
    store = CsvStore(settings.paths.data_dir, lock_timeout=settings.storage.lock_timeout_seconds)
    try:
        v0 = latest_v0(store.matches.read_all(), store.matches_flagged.read_all())
    except CsvStoreError as exc:
        typer.echo(f"Cannot read v0 matches: {exc}")
        raise typer.Exit(1) from exc
    newest: dict[str, TrialResult] = {}
    for path in sorted(judgments_dir(settings.paths.data_dir).glob("*.json")):
        r = TrialResult.model_validate_json(path.read_text(encoding="utf-8"))
        if r.prompt_version != PROMPT_VERSION or r.judged is None or r.judged.output is None:
            continue
        if r.job_id not in newest or r.usage.at > newest[r.job_id].usage.at:
            newest[r.job_id] = r
    result, rows = compare(read_labels(labels or _default_labels(settings)), newest.values(), v0)

    typer.echo(f"v0: latest scoring version {result.v0_version} ({result.v0_created_at})")
    typer.echo(f"Gemini: prompt {PROMPT_VERSION} · {len(newest)} job(s) judged")
    typer.echo(
        f"Labelled pairs in judged jobs: {result.labelled_pairs} in {result.jobs_judged} job(s)"
        f" · without a Gemini verdict: {result.pairs_without_verdict}"
    )
    typer.echo("\nLabel -> Gemini verdict (fit / partial / no / missing)")
    for label, counts in result.confusion.items():
        typer.echo(f"  {label:<8} " + " / ".join(str(counts[k]) for k in counts))
    typer.echo(
        f"Exact agreement {result.exact_agreement} · kappa {result.kappa} "
        f"(labels both AI judges agreed on: {result.kappa_agreed_labels})"
    )
    typer.echo("\nPut in front of the reviewer (surfaced / labelled), by label")
    for s in (result.gemini_surfaced, result.v0_surfaced, result.v0_surfaced_with_flagged):
        detail = " · ".join(f"{k} {a}/{b}" for k, (a, b) in s.by_label.items())
        typer.echo(
            f"  {s.system:<26} {detail} · fits among surfaced {s.fit_share} · "
            f"'no' among surfaced {s.no_share}"
        )
    d = result.within_job_difference
    typer.echo(
        f"\nWithin a job, fit ranked above 'no' ({result.within_job_pairs} pairs): Gemini "
        f"{result.within_job_gemini} · v0 shortlist {result.within_job_v0_shortlist}"
        + (f" · difference {d.mean} (95% {d.low} to {d.high}, by job)" if d else "")
    )
    j = result.jobs
    typer.echo(
        f"\nJobs: {j.jobs} · with a labelled fit {j.with_labelled_fit} (Gemini shows nothing for "
        f"{j.gemini_nothing_but_fit}, v0 for {j.v0_nothing_but_fit}) · without "
        f"{j.without_labelled_fit} (Gemini shows nothing for {j.gemini_nothing_and_no_fit}, v0 "
        f"for {j.v0_nothing_and_no_fit})"
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = settings.paths.data_dir / "llm_results" / f"compare-{stamp}"
    from jobmatch.llm.compare import PairRow

    export_csv(PairRow, rows, out.with_suffix(".csv"))
    out.with_suffix(".json").write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    typer.echo(f"\nWrote {out}.csv (pairs, talent IDs only) and {out}.json")
    log.info("llm_compare", extra={"fields": json.loads(result.model_dump_json())})
