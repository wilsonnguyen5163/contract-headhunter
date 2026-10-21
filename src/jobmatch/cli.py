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


@app.command("embed-contractors")
def embed_contractors() -> None:
    """(Re)build contractor facet vectors (phase 4)."""
    _not_built(4)
