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
    load_normalization_rules,
    load_settings,
    load_skills_taxonomy,
    load_sources,
)
from jobmatch.logging_setup import configure_logging
from jobmatch.storage.csv_store import CsvStore
from jobmatch.storage.vector_store import NpzVectorStore

if TYPE_CHECKING:
    from jobmatch.embed.build import EmbedReport
    from jobmatch.ingest import IngestReport

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
        load_sources,
    ):
        try:
            loader(settings.config_dir)
        except (OSError, ValueError, ValidationError) as exc:
            problems.append(f"config ({loader.__name__}): {exc}")

    store = CsvStore(root, lock_timeout=settings.storage.lock_timeout_seconds)
    loaded: dict[str, list] = {}
    for table in store.all_tables():
        if not table.path.exists():
            typer.echo(f"-- {table.path.name}: not present")
            continue
        rows, errors = table.check()
        loaded[table.path.name] = rows
        problems.extend(str(e) for e in errors)
        status = "OK" if not errors else f"{len(errors)} problem(s)"
        typer.echo(f"{status:>14} {table.path.name}: {len(rows)} valid row(s)")

    contractor_ids = {c.contractor_id for c in loaded.get("contractors.csv", [])}
    for facet in loaded.get("contractor_facets.csv", []):
        if facet.contractor_id not in contractor_ids:
            problems.append(
                f"contractor_facets.csv: facet {facet.facet_id!r} refers to unknown "
                f"contractor_id {facet.contractor_id!r}"
            )

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
    next to it."""
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
        f"{report.profile_without_talent} profile(s) with no Talent row"
    )
    typer.echo(
        f"  rate: {report.rates_numeric} hourly figure(s) · "
        f"{report.rates_default} set to the default"
    )
    log.info("build_contractors", extra={"fields": report.model_dump()})


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
    """Embed every contractor in the contractor list (only changed ones are re-embedded).
    The first run downloads the embedding model."""
    from jobmatch.config import load_skills_taxonomy
    from jobmatch.embed.build import TextItem, embed_items
    from jobmatch.embed.encoder import Encoder
    from jobmatch.embed.texts import TEMPLATE_HASH, contractor_skills, contractor_text
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
    extractor = SkillExtractor(load_skills_taxonomy(settings.config_dir))
    items = [
        TextItem(id=p.talent_id, text=contractor_text(p, contractor_skills(p, extractor)))
        for p in profiles
    ]
    report = embed_items(
        "contractors",
        items,
        kind="facet",
        encoder=Encoder(settings.embedding),
        store=NpzVectorStore(
            settings.paths.data_dir / "embeddings",
            lock_timeout=settings.storage.lock_timeout_seconds,
        ),
        template_hash=TEMPLATE_HASH,
    )
    _print_embed_report(report)
    log.info("embed_contractors", extra={"fields": report.model_dump()})


@app.command("embed-jobs")
def embed_jobs(ctx: typer.Context) -> None:
    """Embed active jobs in jobs.csv (new, matched, no_match); filtered-out jobs and stubs
    without text are skipped. Only changed jobs are re-embedded."""
    from jobmatch.embed.build import TextItem, embed_items
    from jobmatch.embed.encoder import Encoder
    from jobmatch.embed.texts import TEMPLATE_HASH, job_text
    from jobmatch.schemas import JobStatus
    from jobmatch.storage.csv_store import CsvJobRepository

    settings: Settings = ctx.obj
    store = CsvStore(settings.paths.data_dir, lock_timeout=settings.storage.lock_timeout_seconds)
    active = {JobStatus.NEW, JobStatus.MATCHED, JobStatus.NO_MATCH}
    jobs = [j for j in CsvJobRepository(store).list_all() if j.status in active]
    report = embed_items(
        "jobs",
        [TextItem(id=j.job_id, text=job_text(j)) for j in jobs],
        kind="job",
        encoder=Encoder(settings.embedding),
        store=NpzVectorStore(
            settings.paths.data_dir / "embeddings",
            lock_timeout=settings.storage.lock_timeout_seconds,
        ),
        template_hash=TEMPLATE_HASH,
    )
    _print_embed_report(report)
    log.info("embed_jobs", extra={"fields": report.model_dump()})
