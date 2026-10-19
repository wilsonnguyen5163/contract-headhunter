"""jobmatch command line. Run with `python -m jobmatch <command>`."""

import logging
from pathlib import Path
from typing import Annotated

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
)
from jobmatch.logging_setup import configure_logging
from jobmatch.storage.csv_store import CsvStore
from jobmatch.storage.vector_store import NpzVectorStore

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


@app.command("ingest-manual")
def ingest_manual(file: Path) -> None:
    """Import manually collected jobs (phase 3)."""
    _not_built(3)


@app.command("embed-contractors")
def embed_contractors() -> None:
    """(Re)build contractor facet vectors (phase 4)."""
    _not_built(4)
