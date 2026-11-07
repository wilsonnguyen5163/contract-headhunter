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

if TYPE_CHECKING:
    from jobmatch.config import LlmSettings
    from jobmatch.ingest import IngestReport
    from jobmatch.llm.anonymize import Roster
    from jobmatch.llm.gemini import GeminiProvider
    from jobmatch.schemas import ContractorProfile, Job
    from jobmatch.storage.repositories import DataLoader

app = typer.Typer(no_args_is_help=True, add_completion=False)
log = logging.getLogger("jobmatch.cli")


def _loader(settings: Settings, data_dir: Path | None = None) -> "DataLoader":
    """The storage backend from settings (storage.backend), or exit 1 with the reason."""
    from jobmatch.storage.loader import StorageBackendUnavailable, create_loader

    try:
        return create_loader(settings, data_dir=data_dir)
    except StorageBackendUnavailable as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc


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
    """Check config and every CSV file against their schemas (the CSV storage backend)."""
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

    if problems:
        typer.echo(f"\n{len(problems)} problem(s):")
        for problem in problems:
            typer.echo(f"  - {problem}")
        raise typer.Exit(1)
    typer.echo("\nAll data valid.")


@app.command("run-once")
def run_once(
    ctx: typer.Context,
    contractors: Annotated[
        Path | None,
        typer.Option(
            "--contractors",
            help="Contractor file to load first (.xlsx workbook or .csv): new talent IDs are "
            "added, changed ones updated, others kept.",
        ),
    ] = None,
    max_calls: Annotated[
        int | None, typer.Option("--max-calls", min=1, help="Send at most this many calls.")
    ] = None,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="Go ahead when the run would use most of today's quota (or, if it cannot "
            "finish today, send what fits).",
        ),
    ] = False,
) -> None:
    """v1 run: load contractors (optional), import jobs from data/inbox/, judge new jobs with
    Gemini (one call per job, cached), store shortlists in data/llm_matches.csv. Never uses
    the v0 matcher."""
    from jobmatch.pipeline import PipelineError
    from jobmatch.pipeline import run_once as run_pipeline
    from jobmatch.sources.contractor_upsert import ContractorFileError
    from jobmatch.storage.csv_store import CsvStoreError
    from jobmatch.storage.loader import StorageBackendUnavailable

    settings: Settings = ctx.obj
    llm = settings.llm
    if llm is None:
        typer.echo("settings.yaml has no llm section.")
        raise typer.Exit(1)
    try:
        report = run_pipeline(
            settings,
            provider_factory=lambda: _gemini(llm),
            contractor_file=contractors,
            confirm=lambda check: yes,
            max_calls=max_calls,
            echo=typer.echo,
        )
    except (PipelineError, ContractorFileError, CsvStoreError, StorageBackendUnavailable) as exc:
        typer.echo(f"Stopped: {exc}")
        raise typer.Exit(1) from exc
    if report.budget and report.budget.verdict != "ok" and not report.confirmed:
        typer.echo("Add --yes to go ahead, or --max-calls to send fewer.")
    typer.echo(f"Run {report.run_id} · {report.seconds} s")
    log.info("run_once", extra={"fields": report.model_dump(mode="json", exclude={"ingest"})})
    if report.stopped or (report.judge and report.judge.failed):
        raise typer.Exit(1)


@app.command("export-llm-matches")
def export_llm_matches(ctx: typer.Context) -> None:
    """Write each active job's current Gemini shortlist to exports/llm-matches-<time>.csv for
    Excel (jobs with no match get one row). No API call. The file holds contractor names:
    keep it local."""
    from jobmatch.llm.export import export_current

    settings: Settings = ctx.obj
    result = export_current(settings)
    typer.echo(f"Wrote {result.rows} row(s) for {result.jobs} job(s) to {result.path}")
    log.info("export_llm_matches", extra={"fields": {"rows": result.rows, "jobs": result.jobs}})


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
        loader=_loader(settings, data_dir),
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
            loader=_loader(settings, data_dir),
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
    if dest.resolve() == settings.paths.contractors_csv.resolve():
        # The app's own contractor list: written through the storage backend.
        from jobmatch.storage.loader import create_loader

        saved = create_loader(settings).contractors.replace_all(rows)
        if saved:
            typer.echo(f"Previous list kept at {saved}")
    else:  # any other file: a plain export, after a backup of the old one
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
            "bash scripts/reset_reimport.sh."
        )
    log.info("learn_contractor_skills", extra={"fields": report.model_dump()})


# ---- v1: Gemini matching (doc/V1_PLAN.md) ----


def _llm_inputs(
    settings: Settings,
) -> "tuple[LlmSettings, DataLoader, list[Job], list[ContractorProfile], Roster]":
    """The llm settings, active jobs, contractor list and anonymized roster, or exit 1 with
    the reason. Prints counts only (talent IDs, never names)."""
    from jobmatch.config import load_professions
    from jobmatch.llm.anonymize import build_roster
    from jobmatch.llm.payload import ACTIVE_STATUSES
    from jobmatch.llm.preview import profession_problems
    from jobmatch.storage.csv_store import CsvStoreError

    llm = settings.llm
    if llm is None:
        typer.echo("settings.yaml has no llm section.")
        raise typer.Exit(1)
    loader = _loader(settings)
    if not loader.contractors.exists():
        typer.echo("No contractor list yet. Run build-contractors first.")
        raise typer.Exit(1)
    try:
        jobs = [j for j in loader.jobs.list_all() if j.status in ACTIVE_STATUSES]
        profiles = loader.contractors.list_all()
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
    return llm, loader, jobs, profiles, roster


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
    llm, loader, jobs, profiles, roster = _llm_inputs(settings)

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
            llm.limits, llm.budget, UsageLedger(loader.llm_usage).today(), s.calls, tokens_per_call
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
    llm, loader, _, _, roster = _llm_inputs(settings)
    professions = load_professions(settings.config_dir)
    if professions is None or not roster.cards:
        typer.echo("Nothing to ask: professions.yaml or contractor cards are missing.")
        raise typer.Exit(1)
    call = prepare_profession_call(roster, professions, llm)
    ledger = UsageLedger(loader.llm_usage)
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
    from jobmatch.storage.csv_store import CsvStoreError

    settings: Settings = ctx.obj
    loader = _loader(settings)
    try:
        rows = read_suggestions(suggestions)
        profiles = loader.contractors.list_all()
    except (OSError, ValueError, CsvStoreError) as exc:
        typer.echo(f"Cannot read: {exc}")
        raise typer.Exit(1) from exc
    report = apply_suggestions(profiles, rows, loader.contractors)
    if report.backup:
        typer.echo(f"Previous list kept at {report.backup}")
    typer.echo(f"Updated {report.updated} contractor(s) · {report.unchanged} unchanged")
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
    llm, loader, jobs, profiles, roster = _llm_inputs(settings)
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
    ledger = UsageLedger(loader.llm_usage)
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
    llm, loader, jobs, _, roster = _llm_inputs(settings)
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
    ledger = UsageLedger(loader.llm_usage)
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
    """Compare Gemini's cached verdicts (current prompt version) with the blind labels. No API
    call. Writes the pair-by-pair table to data/llm_results/."""
    import json
    from datetime import UTC, datetime

    from jobmatch.llm.compare import PairRow, compare, read_labels
    from jobmatch.llm.judge import judgments_dir
    from jobmatch.llm.prompt import PROMPT_VERSION
    from jobmatch.llm.trial import TrialResult
    from jobmatch.storage.csv_store import export_csv

    settings: Settings = ctx.obj
    newest: dict[str, TrialResult] = {}
    for path in sorted(judgments_dir(settings.paths.data_dir).glob("*.json")):
        r = TrialResult.model_validate_json(path.read_text(encoding="utf-8"))
        if r.prompt_version != PROMPT_VERSION or r.judged is None or r.judged.output is None:
            continue
        if r.job_id not in newest or r.usage.at > newest[r.job_id].usage.at:
            newest[r.job_id] = r
    result, rows = compare(read_labels(labels or _default_labels(settings)), newest.values())

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
    s = result.gemini_surfaced
    detail = " · ".join(f"{k} {a}/{b}" for k, (a, b) in s.by_label.items())
    typer.echo(
        f"\nPut in front of the reviewer (fit or partial / labelled): {detail} · fits among "
        f"surfaced {s.fit_share} · 'no' among surfaced {s.no_share}"
    )
    typer.echo(
        f"Within a job, fit ranked above 'no' ({result.within_job_pairs} pairs): "
        f"{result.within_job_gemini}"
    )
    j = result.jobs
    typer.echo(
        f"\nJobs: {j.jobs} · with a labelled fit {j.with_labelled_fit} (Gemini shows nothing for "
        f"{j.gemini_nothing_but_fit}) · without {j.without_labelled_fit} (Gemini shows nothing "
        f"for {j.gemini_nothing_and_no_fit})"
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = settings.paths.data_dir / "llm_results" / f"compare-{stamp}"
    export_csv(PairRow, rows, out.with_suffix(".csv"))
    out.with_suffix(".json").write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    typer.echo(f"\nWrote {out}.csv (pairs, talent IDs only) and {out}.json")
    log.info("llm_compare", extra={"fields": json.loads(result.model_dump_json())})
