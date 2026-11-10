"""The desktop app's screens (NiceGUI). All logic lives in app/service.py; this file only
lays out the screens and wires buttons to it.

Screens:
- "/"         Find matches: pick the contractor folder and a jobs file, run, watch progress.
- "/results"  One card per job: up to 3 fits, or the best partial to check, or no match;
              approve / reject / needs info on each; export to Excel; start a new search.
- "/jobs"     Every job A–Z, coloured by state (matched / no match / waiting / closed), with
              an Unavailable toggle, Reopen and Delete for closed jobs (a job is completed
              when an application is set to Hired).
- "/talent"   Every contractor, with the "Available for matching" toggle (the reviewer's
              call), the jobs they were approved for and their résumé. Profiles are
              read-only here.
- "/settings" Gemini key (never shown back), reviewer name, data folder.

The run is blocking, so it goes to a worker thread (run.io_bound). Its progress lines reach
the screen through a queue read by a timer; its budget question comes back to the screen as
a dialog (run_coroutine_threadsafe), and the worker waits for the answer.
"""

import asyncio
import queue
import re
import subprocess
import sys
import unicodedata
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import FileResponse
from nicegui import app as nicegui_app
from nicegui import run, ui
from nicegui.events import (
    GenericEventArguments,
    UploadEventArguments,
    ValueChangeEventArguments,
)

from jobmatch.app.service import (
    JOB_SUFFIXES,
    UPLOAD_SUFFIXES,
    AppError,
    AppService,
    ContractorFileInfo,
    JobAction,
    JobOverview,
    JobResult,
    JobState,
    MatchView,
    TalentOverview,
)
from jobmatch.app_paths import APP_TITLE
from jobmatch.backup import BackupInfo, backup_name
from jobmatch.llm.matches import MEETS_SEP
from jobmatch.llm.usage import RunCheck
from jobmatch.pipeline import PipelineError, RunOnceReport
from jobmatch.schemas import (
    ApplicationStatus,
    Commitment,
    Decision,
    Job,
    JobAvailability,
    ReasonCode,
    RemoteType,
    Review,
)
from jobmatch.sources.contractor_upsert import ContractorFileError
from jobmatch.storage.csv_store import CsvStoreError
from jobmatch.storage.loader import StorageBackendUnavailable

# Screen width follows the window: full width, side padding growing with the window, capped
# at 1600 px so lines stay readable on very wide monitors. Settings is a short form: narrower.
PAGE = "w-full mx-auto max-w-[1600px] px-2 sm:px-4 lg:px-8"
PAGE_NARROW = "w-full mx-auto max-w-[900px] px-2 sm:px-4 lg:px-8"

# The org's theme, light purple. The header and main buttons use a medium purple so white
# text on them stays readable; the page background is the light purple. The decision colours
# (SHADE below: green / yellow / red) carry meaning and are not part of the theme.
THEME_PRIMARY = "#7E57C2"
THEME_BACKGROUND = "#EDE7F6"

RUN_ERRORS = (
    AppError,
    PipelineError,
    ContractorFileError,
    CsvStoreError,
    StorageBackendUnavailable,
    OSError,
)

OUTCOME_TEXT = {
    "fits": ("Clear fits", "positive"),
    "check_partial": ("No clear fit: best partial match, check by hand", "warning"),
    "no_match": ("No match: nobody in the contractor list fits", "grey"),
    "not_judged": ("Not judged yet: click Find matches", "grey"),
}
REASON_TEXT = {
    ReasonCode.WRONG_SKILLS: "Wrong skills",
    ReasonCode.SENIORITY: "Seniority",
    ReasonCode.LOCATION: "Location",
    ReasonCode.LANGUAGE: "Language",
    ReasonCode.NOT_CONTRACT: "Not a contract",
    ReasonCode.DUPLICATE: "Duplicate",
    ReasonCode.EXPIRED: "Posting expired",
    ReasonCode.OTHER: "Other",
}
DECISION_TEXT = {
    Decision.APPROVED: ("Approved", "positive"),
    Decision.REJECTED: ("Rejected", "negative"),
    Decision.NEEDS_INFO: ("Needs info", "warning"),
}


def open_in_file_manager(path: Path) -> None:
    """Show a file or folder in Finder / Explorer (fixed arguments, no shell)."""
    target = path if path.exists() else path.parent
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(target)], check=False)
    elif sys.platform == "win32":
        subprocess.run(["explorer", f"/select,{target}"], check=False)
    else:
        subprocess.run(
            ["xdg-open", str(target.parent if target.is_file() else target)], check=False
        )


# ---- file picking: the OS dialog in the app, an upload box in the browser preview ----


NOTHING_PICKED = "Nothing picked yet"

# Browser preview: a folder picked with the browser's chooser. Keeps the contractor file at
# the top and the PDFs in resume/ (or resumes/), tells the server what was picked, then hands
# the kept files to the hidden upload box. File contents never pass through this script.
_FOLDER_JS = """(e) => {
  const all = [...e.target.files];
  const folder = all.length ? all[0].webkitRelativePath.split('/')[0] : '';
  let contractorFiles = 0;
  const keep = all.filter((f) => {
    const parts = f.webkitRelativePath.split('/');
    const name = parts[parts.length - 1].toLowerCase();
    if (name.startsWith('.') || name.startsWith('~$')) return false;
    if (parts.length === 2 && (name.endsWith('.xlsx') || name.endsWith('.csv'))) {
      contractorFiles += 1;
      return true;
    }
    return parts.length === 3 && ['resume', 'resumes'].includes(parts[1].toLowerCase())
      && name.endsWith('.pdf');
  });
  emit({folder: folder, files: keep.length, contractor_files: contractorFiles});
  if (contractorFiles === 1) getElement(UPLOAD_ID).$refs.qRef.addFiles(keep);
  e.target.value = '';
}"""


def pick_row(label: str, icon: str, marker: str) -> tuple[ui.button, ui.label]:
    """The standard picker (user request): an icon button, then the picked path beside it."""
    with ui.row().classes("w-full items-center gap-3 no-wrap"):
        button = (
            ui.button(label, icon=icon)
            .props("outline no-caps")
            .classes("bg-white shrink-0")
            .mark(marker)
        )
        note = (
            ui.label(NOTHING_PICKED)
            .classes("text-sm text-grey-7 font-mono truncate min-w-0")
            .mark(f"{marker}-path")
        )
    return button, note


def file_picker(
    native: bool,
    suffixes: tuple[str, ...],
    label: str,
    on_file: Callable[[str, bytes, Path | None], Awaitable[None] | None],
    *,
    multiple: bool = False,
    button: str = "Choose files…",
    icon: str = "upload_file",
    marker: str = "file-picker",
) -> Callable[[], None]:
    """The standard picker row for files: the OS dialog in the app; in the browser preview
    the same button opens the browser's file chooser (a hidden upload box), and the note
    shows file names (a browser never reveals paths). on_file is called once per file, in
    order. Returns a function that clears the note (after a run)."""
    chooser, note = pick_row(button, icon, marker)
    names: list[str] = []

    def reset() -> None:
        names.clear()
        note.text = NOTHING_PICKED

    async def handle(name: str, data: bytes, path: Path | None) -> None:
        result = on_file(name, data, path)
        if asyncio.iscoroutine(result):
            await result
        names.append(str(path) if path else name)
        note.text = ", ".join(names)
        note.tooltip(note.text)

    if native:

        async def browse() -> None:
            window = nicegui_app.native.main_window
            if window is None:
                ui.notify("The file dialog is not available.", type="negative")
                return
            patterns = ";".join(f"*{s}" for s in suffixes)
            picked = await window.create_file_dialog(
                allow_multiple=multiple, file_types=(f"{label} ({patterns})",)
            )
            for chosen in picked or ():
                path = Path(chosen)
                await handle(path.name, path.read_bytes(), path)

        chooser.on_click(browse)
    else:

        async def uploaded(e: UploadEventArguments) -> None:
            await handle(e.file.name, await e.file.read(), None)

        upload = (
            ui.upload(on_upload=uploaded, auto_upload=True, multiple=multiple)
            .props(f'accept="{",".join(suffixes)}"')
            .classes("hidden")
            .mark(f"{marker}-upload")
        )
        chooser.on_click(lambda: upload.run_method("pickFiles"))
    return reset


# ---- "Add a job by hand" (user decisions 2026-11-10) ----

# (field, label, hint, kind): kind "line", "date" or "list" (one item per line).
JOB_FORM_FIELDS = [
    ("url", "Job posting link",
     "The address of the posting. Used to recognise the same job if it comes in again.", "line"),
    ("job_title", "Job title", "As written in the posting, e.g. Data Analyst (6-month contract).",
     "line"),
    ("company", "Company", "The hiring company, or the agency if that's all the posting names.",
     "line"),
    ("location", "Location", "City and province, e.g. Toronto, ON.", "line"),
    ("work_mode", "Work mode", "Where the work happens, as the posting says.", "work_mode"),
    ("hours_per_week", "Hours per week",
     "A number, e.g. 40 or 37.5. {ft} or more shows as FT, fewer as PT.", "hours"),
    ("pay", "Pay", "Canadian dollars: a number, or a range (the lower end is kept), e.g. 90 or "
     "85–95.", "line"),
    ("pay_type", "Pay type", "Hourly rate, or yearly salary.", "pay_type"),
    ("posted_date", "Posted date", "When the job was posted. Leave empty if unknown.", "date"),
    ("responsibility", "Responsibilities", "One per line. What the person will do.", "list"),
    ("qualification", "Qualifications",
     "One per line. Required skills, experience, languages.", "list"),
]  # fmt: skip


WORK_MODE_CHOICES = {"": "Not stated", "on-site": "On-site", "hybrid": "Hybrid", "remote": "Remote"}
PAY_TYPE_CHOICES = {"hourly": "Hourly", "salary": "Salary (per year)"}
FORM_DEFAULTS = {"work_mode": "", "pay_type": "hourly"}


def job_form(service: AppService, on_saved: Callable[[], None]) -> None:
    """One job typed or pasted by the reviewer. Saved as given (only cleaned of formatting);
    the next Ingest or Find matches stores it."""
    inputs: dict[str, ui.input | ui.textarea | ui.select] = {}
    with (
        ui.dialog() as dialog,
        ui.card().classes("job-form").style("width: 760px; max-width: 95vw"),
    ):
        ui.label("Add a job by hand").classes("text-h6")
        ui.html(
            "Paste or type the details. Formatting from the posting (bullets, bold, extra "
            'spaces and empty lines) is removed. <span class="required-mark">*</span> Required.'
        ).classes("text-sm text-grey-8")
        with ui.grid(columns=2).classes("w-full gap-x-4 gap-y-1"):
            full_time = service.settings.normalize.full_time_hours_per_week
            for name, label, hint, kind in JOB_FORM_FIELDS:
                hint = hint.replace("{ft}", f"{full_time:g}")  # noqa: PLW2901
                if kind == "list":
                    field = ui.textarea(label).props(f'outlined autogrow hint="{hint}"')
                    field.classes("col-span-2")
                elif kind in ("work_mode", "pay_type"):
                    choices = WORK_MODE_CHOICES if kind == "work_mode" else PAY_TYPE_CHOICES
                    field = ui.select(choices, label=label, value=FORM_DEFAULTS[name]).props(
                        f'outlined dense hint="{hint}"'
                    )
                elif kind == "hours":
                    field = ui.input(label).props(
                        f'outlined dense type=number min=1 max=80 step=0.5 hint="{hint}"'
                    )
                else:
                    field = ui.input(label).props(f'outlined dense hint="{hint}"')
                    if kind == "date":
                        field.props("type=date stack-label")
                    if name in REQUIRED_JOB_FIELDS:
                        field.classes("col-span-2 required")
                inputs[name] = field.mark(f"job-field:{name}")

        def save(again: bool) -> None:
            try:
                entry = service.add_job_entry({k: str(f.value or "") for k, f in inputs.items()})
            except AppError as exc:
                ui.notify(str(exc), type="warning", multi_line=True)
                return
            ui.notify(f"Saved {entry.job_title}. Stored on the next Ingest.", type="positive")
            on_saved()
            if again:
                for key, f in inputs.items():
                    f.value = FORM_DEFAULTS.get(key, "")
            else:
                dialog.close()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
            ui.button("Save and add another", on_click=lambda: save(True)).props(
                "outline no-caps"
            ).mark("job-save-another")
            ui.button("Save", icon="save", on_click=lambda: save(False)).props("no-caps").mark(
                "job-save"
            )
    dialog.open()


# ---- screen: find matches ----


# The form's hints (user request 2026-11-10): flush with the box's left edge, right under it.
# A required field's hint starts with a red "*" (outside the box, not in its label).
JOB_FORM_CSS = (
    ".job-form .q-field__bottom { padding: 2px 0 0 1px; }"
    ".job-form .q-field--with-bottom { padding-bottom: 18px; }"
    ".job-form .required .q-field__messages > div::before"
    " { content: '* '; color: var(--q-negative); font-weight: 700; }"
    ".job-form .required-mark { color: var(--q-negative); font-weight: 700; }"
)
REQUIRED_JOB_FIELDS = ("url", "job_title")


def home_page(service: AppService, native: bool) -> None:
    ui.add_css(JOB_FORM_CSS)
    with ui.column().classes(f"{PAGE} gap-4") as page:
        if not service.has_key():
            with ui.card().classes("w-full bg-amber-50"):
                ui.label("Add your Gemini key in Settings before the first run.")
                ui.button("Open Settings", on_click=lambda: ui.navigate.to("/settings")).props(
                    "flat"
                )

        with ui.card().classes("w-full gap-2"):
            ui.label("Contractors (optional)").classes("text-h6")
            count = ui.label().classes("text-sm text-grey-8")

            def show_count() -> None:
                count.text = f"{service.contractor_count()} contractor(s) processed."

            show_count()
            picked = ui.label().classes("text-sm").mark("contractor-picked")
            skipped = ui.label().classes("text-sm text-orange-9").mark("resumes-skipped")

            def show(info: ContractorFileInfo) -> None:
                what = []
                if info.path is not None:
                    what.append(f"{info.path.name}: {info.contractors} contractor(s)")
                what.append(f"{info.resumes} résumé(s)")
                picked.text = " · ".join(what) + ", stored when you click Ingest or Find matches."
                picked.set_visibility(True)
                skipped.text = f"Skipped (not named by a talent ID): {', '.join(info.unmatched)}"
                skipped.set_visibility(bool(info.unmatched))

            if native:
                chooser, folder_note = pick_row(
                    "Choose folder…", "folder_open", "contractor-folder"
                )

                def reset_contractors() -> None:
                    folder_note.text = NOTHING_PICKED

                async def browse() -> None:
                    window = nicegui_app.native.main_window
                    if window is None:
                        ui.notify("The folder dialog is not available.", type="negative")
                        return
                    import webview  # only in the desktop app

                    chosen = await window.create_file_dialog(webview.FileDialog.FOLDER)
                    if not chosen:
                        return
                    try:
                        info = service.pick_contractor_folder(Path(chosen[0]))
                    except AppError as exc:
                        ui.notify(str(exc), type="negative", multi_line=True)
                        return
                    folder_note.text = str(chosen[0])
                    folder_note.tooltip(folder_note.text)
                    show(info)

                chooser.on_click(browse)
            else:
                # The browser's own folder chooser (user request): only the top-level
                # contractor file and the PDFs in resume/ are sent, then checked one by one.
                chooser, folder_note = pick_row(
                    "Choose folder…", "folder_open", "contractor-folder"
                )

                def reset_contractors() -> None:
                    folder_note.text = NOTHING_PICKED

                async def contractor(e: UploadEventArguments) -> None:
                    try:
                        info = service.pick_contractor_file(e.file.name, data=await e.file.read())
                    except AppError as exc:
                        ui.notify(str(exc), type="negative", multi_line=True)
                        return
                    show(info)

                def chosen(e: GenericEventArguments) -> None:
                    service.clear_contractor_picks()
                    folder, files = str(e.args.get("folder", "")), int(e.args.get("files", 0))
                    contractor_files = int(e.args.get("contractor_files", 0))
                    folder_note.text = folder or NOTHING_PICKED
                    if contractor_files != 1:
                        ui.notify(
                            f"{folder}: expected exactly one contractor file (.xlsx or .csv) at "
                            f"the top of the folder, found {contractor_files}.",
                            type="negative",
                            multi_line=True,
                        )
                    elif not files:
                        ui.notify(f"{folder}: nothing to load.", type="warning")

                upload = (
                    ui.upload(on_upload=contractor, auto_upload=True, multiple=True)
                    .props(f'accept="{",".join(UPLOAD_SUFFIXES)}"')
                    .classes("hidden")
                    .mark("contractor-folder-upload")
                )
                folder_input = (
                    ui.element("input")
                    .props("type=file webkitdirectory multiple")
                    .classes("hidden")
                )
                folder_input.on(
                    "change",
                    chosen,
                    js_handler=_FOLDER_JS.replace("UPLOAD_ID", str(upload.id)),
                )
                # Opened in the browser itself: a file chooser needs the click to be direct.
                chooser.on("click", js_handler=f"() => getHtmlElement({folder_input.id}).click()")
            # Under the picker row: what was picked, then any skipped PDFs.
            picked.move(target_index=-1)
            skipped.move(target_index=-1)
            picked.set_visibility(False)
            skipped.set_visibility(False)

        with ui.card().classes("w-full gap-2"):
            ui.label("Jobs").classes("text-h6")
            ui.label(
                "A jobs CSV (one row per posting, from the template), or add jobs one at a time "
                "by hand. Jobs already imported are skipped."
            ).classes("text-sm text-grey-8")

            added = ui.column().classes("gap-0")

            def jobs(name: str, data: bytes, path: Path | None) -> None:
                try:
                    info = service.add_jobs_file(name, data)
                except AppError as exc:
                    ui.notify(str(exc), type="negative", multi_line=True)
                    return
                if info.jobs is not None:
                    what = f"{info.jobs} job(s)"
                else:
                    what = f"{info.links} link(s)" if info.kind == "links" else "a posting"
                with added:
                    ui.label(f"Added {name}: {what}.").classes("text-sm")
                    for problem in info.problems:
                        ui.label(f"Skipped: {problem}").classes("text-sm text-orange-9").mark(
                            "jobs-row-problem"
                        )

            reset_jobs = file_picker(
                native, JOB_SUFFIXES, "Jobs files", jobs, multiple=True, marker="jobs-files"
            )
            with ui.row().classes("items-center gap-2"):
                ui.button(
                    "Add a job by hand",
                    icon="edit_note",
                    on_click=lambda: job_form(service, pending.refresh),
                ).props("outline no-caps").classes("bg-white").mark("add-job")

                def template() -> None:
                    path = service.job_template()
                    if native:
                        open_in_file_manager(path.parent)
                        ui.notify(f"Template saved: {path}", type="positive", multi_line=True)
                    else:
                        ui.download(path, "jobs-template.csv")

                ui.button("Get CSV template", icon="download", on_click=template).props(
                    "flat no-caps"
                ).mark("job-template")

            @ui.refreshable
            def pending() -> None:
                entries = service.pending_entries()
                if not entries:
                    return
                ui.label(
                    f"Entered by hand ({len(entries)}), stored on the next Ingest or Find matches:"
                ).classes("text-sm font-medium")
                for e in entries:
                    with ui.row().classes("items-center gap-1 no-wrap text-sm"):
                        ui.icon("edit_note", size="18px").classes("text-grey-7")
                        ui.label(" · ".join(p for p in (e.title, e.company) if p))

                        def remove(name: str = e.name, title: str = e.title) -> None:
                            def go() -> None:
                                try:
                                    service.remove_entry(name)
                                except AppError as exc:
                                    ui.notify(str(exc), type="warning")
                                pending.refresh()

                            confirm_delete(
                                f"Remove {title}?", ["This entry is not stored yet."], go
                            )

                        ui.button(icon="close", on_click=remove).props(
                            'flat round dense size=sm aria-label="Remove entry"'
                        ).mark(f"remove-entry:{e.name}").tooltip("Remove this entry")

            pending()
            added.move(target_index=-1)

        with ui.column().classes("w-full items-center gap-2 py-2"):
            with ui.row().classes("items-center justify-center gap-3"):
                # Ingest: store contractors and jobs only, to check them before matching.
                ingest_button = (
                    ui.button("Ingest", icon="move_to_inbox")
                    .props("size=lg outline color=primary no-caps")
                    .classes("bg-white")
                    .mark("ingest")
                    .tooltip("Store the picked contractors and jobs without matching")
                )
                run_button = (
                    ui.button("Find matches", icon="search")
                    .props("size=lg color=primary")
                    .mark("find-matches")
                )
            stage = ui.label("").classes("text-sm text-grey-8")
            bar = ui.linear_progress(value=0, show_value=False).props("rounded size=12px")
            bar.classes("w-full sm:w-3/4 lg:w-1/2")
            bar.set_visibility(False)
            status = ui.label("").classes("text-sm text-center")
        log = ui.log(max_lines=400).classes("w-full h-[33vh] min-h-[200px] font-mono text-xs")
        quota = service.quota()
        quota_label = ui.label(
            f"Gemini today: {quota.requests} of {quota.rpd} requests, {quota.tokens:,} tokens."
        ).classes("text-xs text-grey-7")
        after = ui.row()

    async def ask(check: RunCheck) -> bool:
        with page, ui.dialog().props("persistent") as dialog, ui.card():
            ui.label("Use more of today's Gemini quota?").classes("text-h6")
            ui.label(check.message).classes("text-sm")
            if check.verdict == "exceeds":
                ui.label(
                    f"Yes sends the {check.calls_that_fit} call(s) that fit today; "
                    "the rest wait for the next run."
                ).classes("text-sm")
            with ui.row():
                ui.button("Yes, go ahead", on_click=lambda: dialog.submit(True))
                ui.button("Not now", on_click=lambda: dialog.submit(False)).props("flat")
        answer = await dialog
        dialog.delete()
        return bool(answer)

    async def start(match: bool) -> None:
        loop = asyncio.get_running_loop()
        lines: queue.Queue[str] = queue.Queue()
        latest: dict[str, float | str] = {"share": 0.0, "stage": "Starting"}

        def drain() -> None:
            while not lines.empty():
                log.push(lines.get_nowait())
            bar.value = float(latest["share"])
            stage.text = f"{latest['stage']} ({round(float(latest['share']) * 100)}%)"

        def on_progress(share: float, text: str) -> None:  # called from the worker thread
            latest["share"], latest["stage"] = share, text

        def confirm(check: RunCheck) -> bool:
            return asyncio.run_coroutine_threadsafe(ask(check), loop).result()

        run_button.disable()
        ingest_button.disable()
        after.clear()
        bar.value = 0
        bar.set_visibility(True)
        status.text = (
            "Working… this can take a few minutes when there are new jobs."
            if match
            else "Storing contractors and jobs…"
        )
        timer = ui.timer(0.2, drain)
        try:
            report = await run.io_bound(
                service.run, echo=lines.put, confirm=confirm, progress=on_progress, match=match
            )
        except RUN_ERRORS as exc:
            drain()
            status.text = f"Stopped: {exc}"
            ui.notify(str(exc), type="negative", multi_line=True)
            return
        finally:
            timer.cancel()
            drain()
            run_button.enable()
            ingest_button.enable()
            q = service.quota()
            quota_label.text = (
                f"Gemini today: {q.requests} of {q.rpd} requests, {q.tokens:,} tokens."
            )
        if report is None:
            return
        # The picks are stored now: clear them from the screen.
        reset_contractors()
        reset_jobs()
        pending.refresh()
        picked.set_visibility(False)
        skipped.set_visibility(False)
        added.clear()
        show_count()
        if report.ingest_only:
            status.text = _ingested_text(report)
            return
        status.text = (
            f"Done: {report.jobs_matched} job(s) with suggestions, {report.jobs_no_match} with "
            f"none, {report.jobs_not_judged} not judged yet."
            + (f" {report.stopped}." if report.stopped else "")
        )
        with after.classes("w-full justify-center"):
            ui.button(
                "See results", icon="arrow_forward", on_click=lambda: ui.navigate.to("/results")
            )

    run_button.on_click(lambda: start(match=True))
    ingest_button.on_click(lambda: start(match=False))


def _ingested_text(report: RunOnceReport) -> str:
    parts = []
    if report.contractors is not None:
        c = report.contractors
        parts.append(f"{len(c.added)} contractor(s) added, {len(c.updated)} updated")
    if report.resumes is not None:
        r = report.resumes
        parts.append(f"{r.added + r.replaced} résumé(s) stored")
    new_jobs = sum(i.count("new") + i.count("needs_details") for i in report.ingest)
    parts.append(f"{new_jobs} job(s) imported")
    return "Ingested: " + " · ".join(parts) + ". Nothing sent to Gemini."


# ---- screen: results ----

# Light shades, half way between white and the full colour (Quasar positive / warning /
# negative), so colour codes stay readable behind dark text.
SHADE = {"green": "#90DCA2", "yellow": "#F8DF9B", "red": "#E07F8A"}
# Two steps lighter (about 75% toward white), for the Approve / Reject / Needs info buttons.
SHADE_LIGHT = {"green": "#C8EED0", "yellow": "#FCEFCD", "red": "#F0BFC4"}
TEXT_DARK = "#1D1D1D"
# Verdict chip by fit_score (bands in settings.yaml app.score_bands, user decision
# 2026-11-08): darker green, the green, the yellow, then the light yellow.
SCORE_SHADE = {
    "strong": "#5BBE77",
    "good": SHADE["green"],
    "some": SHADE["yellow"],
    "low": SHADE_LIGHT["yellow"],
}
OUTCOME_SHADE = {"fits": "green", "check_partial": "yellow"}  # no match / not judged: none
DECISION_SHADE = {
    Decision.APPROVED: "green",
    Decision.REJECTED: "red",
    Decision.NEEDS_INFO: "yellow",
}
_MINOR_WORDS = {
    "a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with",
    "au", "aux", "de", "des", "du", "en", "et", "la", "le", "les", "ou", "pour", "sur",
}  # fmt: skip
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?", re.UNICODE)


def title_case(text: str) -> str:
    """Title Case for display only: "senior data analyst (contract)" -> "Senior Data Analyst
    (Contract)". Words that already hold a capital (UX, SQL, iOS, McKinsey) are kept as they
    are; small words (and, of, de, la…) stay lowercase except first."""
    first = True

    def fix(m: re.Match[str]) -> str:
        nonlocal first
        word = m.group(0)
        is_first, first = first, False
        if word != word.lower():
            return word
        if not is_first and word in _MINOR_WORDS:
            return word
        return word[0].upper() + word[1:]

    return _WORD.sub(fix, text)


def _shade_button(text: str, shade: str, on_click: Callable, icon: str | None = None) -> ui.button:
    return (
        ui.button(text, icon=icon, on_click=on_click, color=None)
        .props("unelevated no-caps dense")
        .classes("px-3")
        .style(f"background-color: {SHADE_LIGHT[shade]}; color: {TEXT_DARK};")
    )


def _profile_dialog(service: AppService, talent_id: str, name: str | None) -> None:
    try:
        fields = service.contractor_fields(talent_id)
    except AppError as exc:
        ui.notify(str(exc), type="warning")
        return
    with (
        ui.dialog() as dialog,
        ui.card().style("width: 90vw; max-width: 90vw; max-height: 80vh"),
    ):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(f"Profile: {name or talent_id}").classes("text-h6")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense")
        ui.label("From the contractor list (read-only).").classes("text-xs text-grey-7")
        with (
            ui.scroll_area().classes("w-full h-[65vh]"),
            ui.grid(columns="minmax(10rem, auto) 1fr").classes("w-full gap-x-4 gap-y-1"),
        ):
            ui.label("Column").classes("font-bold border-b")
            ui.label("Value").classes("font-bold border-b")
            for column, values in fields:
                ui.label(column).classes("font-mono text-sm text-grey-8 border-b py-1")
                with ui.column().classes("gap-0 border-b py-1"):
                    if not values:
                        ui.label("—").classes("text-grey-6")
                    for value in values:
                        ui.label(value).classes("text-sm whitespace-pre-wrap")
    dialog.open()


RESUME_ROUTE = "/resume/{talent_id}"
_resume_service: dict[str, AppService] = {}  # the service the résumé route reads from


def _serve_resumes(service: AppService) -> None:
    """GET /resume/<talent_id>: the stored PDF, shown inline. Only files of contractors in
    the list (looked up by ID, never a path from the URL); registered once per process."""
    _resume_service["service"] = service
    if any(getattr(r, "path", None) == RESUME_ROUTE for r in nicegui_app.routes):
        return

    @nicegui_app.get(RESUME_ROUTE)
    def resume(talent_id: str) -> FileResponse:
        path = _resume_service["service"].resume_path(talent_id)
        if path is None:
            raise HTTPException(status_code=404)
        return FileResponse(
            path,
            media_type="application/pdf",
            headers={
                "Content-Disposition": "inline",
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )


def _resume_dialog(talent_id: str, name: str | None) -> None:
    """The résumé in the browser's (or webview's) own PDF viewer."""
    with (
        ui.dialog() as dialog,
        ui.card().style("width: 90vw; max-width: 90vw; height: 90vh; max-height: 90vh"),
    ):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(f"Résumé: {name or talent_id}").classes("text-h6")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense")
        ui.element("iframe").props(
            f'src="/resume/{quote(talent_id, safe="")}" title="Résumé"'
        ).classes("w-full grow border-0").mark("resume-frame")
    dialog.open()


def _meets_table(items: list[str]) -> None:
    """The "Meets" table (v10): TALENT ASSET (their skill or result) | JOB REQUIREMENT (the
    one it meets, summarized). Column names, in capitals: user decision 2026-11-08. Each
    stored item is "skill → requirement"; an older item without a requirement fills only the
    left column."""
    ui.label("Meets").classes("font-bold")
    with (
        ui.element("div")
        .classes("w-full grid grid-cols-2 gap-x-4 gap-y-1 text-sm")
        .mark("meets-table")
    ):
        for header in ("Talent Asset", "Job Requirement"):
            ui.label(header).classes("text-xs text-grey-7 uppercase border-b border-grey-4 pb-1")
        for item in items:
            skill, _, requirement = item.partition(MEETS_SEP)
            ui.label(skill)
            ui.label(requirement or "—").classes("text-grey-9")


def score_shade(service: AppService, score: int | None, fit: bool) -> str:
    """The verdict chip's colour: by fit_score band, or by verdict when there is no score
    (a contractor Gemini judged but did not describe)."""
    if score is None:
        return SHADE["green" if fit else "yellow"]
    bands = service.settings.app.score_bands
    if score >= bands.strong:
        return SCORE_SHADE["strong"]
    if score >= bands.good:
        return SCORE_SHADE["good"]
    if score >= bands.some:
        return SCORE_SHADE["some"]
    return SCORE_SHADE["low"]


def _rejected(review: Review | None) -> bool:
    return review is not None and review.decision is Decision.REJECTED


def _match_row(
    service: AppService, view: MatchView, moved: Callable[[], None] | None = None
) -> None:
    """One contractor on a Results card. `moved`: called when a decision moves them in or
    out of "Show rejected" (a reject, or a reset or approve of a rejection), so the card
    redraws at once (user decision 2026-11-09; Results only)."""
    m = view.match
    state = {"review": view.decision}

    def after(before: Review | None) -> None:
        if moved is not None and _rejected(before) != _rejected(state["review"]):
            moved()
            return
        judgement.refresh()
        paint()

    @ui.refreshable
    def judgement() -> None:
        """Top right: the decision tag and Reset, only once a decision is made."""
        review = state["review"]
        if review is None:
            return
        text, _ = DECISION_TEXT[review.decision]
        reason = f" ({REASON_TEXT[review.reason_code]})" if review.reason_code else ""
        with ui.row().classes("items-center gap-1"):
            ui.label(text + reason).classes("text-sm px-2 py-0.5 rounded").style(
                f"background-color: {SHADE[DECISION_SHADE[review.decision]]}; color: {TEXT_DARK};"
            )
            ui.button(icon="restart_alt", on_click=reset).props("flat round dense size=sm").mark(
                "reset-decision", f"reset:{m.talent_id}"
            ).tooltip("Reset the decision")

    def save(decision: Decision, reason: ReasonCode | None = None, notes: str = "") -> None:
        before = state["review"]
        try:
            state["review"] = service.decide(m.match_id, decision, reason, notes)
        except (AppError, CsvStoreError) as exc:
            ui.notify(str(exc), type="negative")
            return
        after(before)

    def reset() -> None:
        before = state["review"]
        try:
            service.clear_decision(m.match_id)
        except CsvStoreError as exc:
            ui.notify(str(exc), type="negative")
            return
        state["review"] = None
        after(before)

    async def reject() -> None:
        with ui.dialog() as dialog, ui.card():
            ui.label("Why reject this match?").classes("text-h6")
            reason = (
                ui.select({code.value: text for code, text in REASON_TEXT.items()}, label="Reason")
                .classes("w-64")
                .mark("reject-reason")
            )
            notes = ui.input("Notes (optional)").classes("w-64")
            with ui.row():
                ui.button("Reject", on_click=lambda: dialog.submit(True)).props(
                    "color=negative"
                ).mark("reject-confirm")
                ui.button("Cancel", on_click=lambda: dialog.submit(False)).props("flat")
        ok = await dialog
        chosen = reason.value
        note = notes.value or ""
        dialog.delete()
        if ok:
            if not chosen:
                ui.notify("Pick a reason for the rejection.", type="warning")
                return
            save(Decision.REJECTED, ReasonCode(chosen), note)

    fit = m.verdict.value == "fit"
    box = ui.column().classes("w-full gap-2 rounded-lg border border-grey-4 p-3")
    box.mark("contractor-box")

    def paint() -> None:
        """Left border in the decision's colour (none when undecided). The width is kept
        constant with padding, so deciding never shifts the contractor's text sideways."""
        review = state["review"]
        if review is None:
            box.style(replace="border-left: 1px solid #BDBDBD; padding-left: calc(0.75rem + 3px);")
        else:
            colour = SHADE[DECISION_SHADE[review.decision]]
            box.style(replace=f"border-left: 4px solid {colour}; padding-left: 0.75rem;")

    paint()
    with box:
        # min-h fits the decision tag and the Reset button, so they appear without moving
        # the contractor's details down.
        with ui.row().classes("w-full items-center justify-between no-wrap min-h-[2.25rem]"):
            with ui.row().classes("items-center gap-2 flex-1 min-w-0"):
                ui.label(view.name or m.talent_id).classes("font-bold")
                if view.headline:
                    ui.label(view.headline).classes("text-grey-8")
                # Only the verdict chip is coloured (by score band); the score is plain text.
                ui.label("fit" if fit else "partial").classes("text-xs px-2 rounded").style(
                    f"background-color: {score_shade(service, m.fit_score, fit)}; "
                    f"color: {TEXT_DARK};"
                ).mark("verdict-chip")
                if m.fit_score is not None:
                    ui.label(f"· score {m.fit_score}").classes("text-xs text-grey-8")
                if not m.detailed:
                    ui.label("reason only").classes("text-xs text-grey-7 italic")
                if view.unavailable:
                    ui.label("Not available for new projects").classes(
                        "text-xs px-2 rounded bg-grey-3 text-grey-9"
                    ).mark("unavailable-tag")
            judgement()
        if m.reason:
            ui.label(m.reason).classes("text-sm")
        with ui.expansion("Details").classes("w-full text-sm"):
            with ui.row().classes("gap-2 pb-2"):
                ui.button(
                    "Profile",
                    icon="badge",
                    on_click=lambda: _profile_dialog(service, m.talent_id, view.name),
                ).props("outline dense no-caps")
                ui.button("Resume", icon="description").props(
                    "outline dense no-caps disable"
                ).tooltip("Resumes come in a later version")
            if m.requirements_met:
                _meets_table(m.requirements_met)
            for title, items in (
                ("Missing", m.requirements_missing),
                ("Flags", m.red_flags),
            ):
                if items:
                    ui.label(title).classes("font-bold")
                    for item in items:
                        ui.label(f"• {item}")
            if m.unverified_evidence:
                ui.label(
                    "Something Gemini listed could not be found in their profile or the job, "
                    "so it was left out."
                ).classes("text-grey-7")
            if not m.detailed:
                ui.label(
                    "Gemini judged this contractor without describing them: only the reason "
                    "above is known."
                ).classes("text-grey-7")
        with ui.row().classes("gap-2 flex-wrap"):
            _shade_button("Approve", "green", lambda: save(Decision.APPROVED), icon="check")
            _shade_button("Reject", "red", reject, icon="close").mark(f"reject:{m.talent_id}")
            _shade_button(
                "Needs info", "yellow", lambda: save(Decision.NEEDS_INFO), icon="help_outline"
            )


def _open_url(url: str) -> Callable[[], None]:
    return lambda: ui.navigate.to(url, new_tab=True)


# ---- contact email and links between Jobs and Results (TODO 1 and 2, 2026-11-10) ----


WORK_MODE_TEXT = {
    RemoteType.ONSITE: "On-site", RemoteType.HYBRID: "Hybrid", RemoteType.REMOTE: "Remote",
}  # fmt: skip
PAY_UNIT_TEXT = {"hour": "hour", "day": "day", "week": "week", "month": "month", "year": "year"}


def pay_text(job: Job) -> str | None:
    """ "$90/hour", "$95,000/year", "$85–95/hour"; None when the pay is not known."""
    if job.pay_min is None and job.pay_max is None:
        return None
    low, high = job.pay_min or job.pay_max, job.pay_max or job.pay_min
    amount = f"${low:,.0f}" if low == high else f"${low:,.0f}–{high:,.0f}"
    unit = f"/{PAY_UNIT_TEXT[job.pay_unit.value]}" if job.pay_unit else ""
    return amount + unit


COMMITMENT_SHORT = {Commitment.FULL_TIME: "FT", Commitment.PART_TIME: "PT"}


def job_title(job: Job, classes: str = "") -> None:
    """The job title, with full-time / part-time after it in smaller text when known
    (user request 2026-11-10), e.g. "Bilingual (Fr/Eng) - PT"."""
    with ui.row().classes(f"items-baseline gap-x-2 gap-y-0 {classes}"):
        ui.label(title_case(job.title_norm or job.title_raw)).classes("text-h6")
        short = COMMITMENT_SHORT.get(job.commitment)
        if short:
            ui.label(f"- {short}").classes("text-sm font-medium text-grey-8").mark(
                f"commitment:{job.job_id}"
            ).tooltip("Full-time" if short == "FT" else "Part-time")


def contact_line(job: Job) -> None:
    """The posting's HR contact email with a copy button, or a note that there is none.
    Shown and copied only: the app never sends email."""
    with ui.row().classes("items-center gap-1 no-wrap text-sm").mark("contact-email"):
        ui.icon("mail_outline", size="16px").classes("text-grey-7")
        if not job.contact_email:
            ui.label("No contact email in the posting").classes("text-grey-7")
            return
        ui.label(job.contact_email).classes("select-all")
        email = job.contact_email

        def copy() -> None:
            ui.clipboard.write(email)
            ui.notify("Email copied.", type="positive")

        ui.button(icon="content_copy", on_click=copy).props(
            'flat round dense size=sm aria-label="Copy email"'
        ).mark(f"copy-email:{job.job_id}").tooltip("Copy email")


def anchor(page: str, job_id: str) -> str:
    """The HTML id of a job's card on a page (job IDs hold ':' and other characters)."""
    return f"{page}-" + re.sub(r"[^A-Za-z0-9_-]", "-", job_id)


FOCUS_KEY = "focus_job"  # app.storage.client: the card the next screen scrolls to


def go_to_card(page: str, job_id: str) -> None:
    """Open Jobs or Results on one job's card (the screens read it on opening)."""
    nicegui_app.storage.client[FOCUS_KEY] = (page, job_id)
    ui.navigate.to(f"/{page}")


def take_focus(page: str) -> str | None:
    """The job this screen was opened for, if any (read once)."""
    focus = nicegui_app.storage.client.pop(FOCUS_KEY, None)
    if focus and focus[0] == page:
        return str(focus[1])
    return None


def scroll_to_card(page: str, job_id: str) -> None:
    """Scroll to the card and outline it in purple for about 2 seconds."""
    target = anchor(page, job_id)
    js = (
        f"const el = document.getElementById('{target}'); if (el) {{"
        " el.scrollIntoView({behavior: 'smooth', block: 'center'});"
        f" el.style.outline = '3px solid {THEME_PRIMARY}'; el.style.outlineOffset = '2px';"
        " setTimeout(() => { el.style.outline = ''; }, 2000); }"
    )
    ui.timer(0.3, lambda: ui.run_javascript(js), once=True)


def _chip(text: str, icon: str, shade: str | None = None) -> None:
    style = f"background-color: {SHADE[shade]};" if shade else "background-color: #F5F5F5;"
    ui.chip(text, icon=icon, color=None, text_color=None).props("dense square").classes(
        "m-0 text-sm"
    ).style(f"{style} color: {TEXT_DARK};")


def _job_card(service: AppService, result: JobResult) -> None:
    """One job on Results. Redraws itself when a reject or a reset moves someone in or out
    of "Show rejected" (that section stays open if it was)."""
    state = {"result": result, "open": False}

    def moved() -> None:
        fresh = service.job_result(result.job.job_id)
        if fresh is not None:
            state["result"] = fresh
        card.refresh()

    def remember(e: ValueChangeEventArguments) -> None:
        state["open"] = bool(e.value)

    @ui.refreshable
    def card() -> None:
        _job_card_body(service, state["result"], moved, bool(state["open"]), remember)

    card()


def _job_card_body(
    service: AppService,
    result: JobResult,
    moved: Callable[[], None],
    rejected_open: bool,
    remember: Callable[[ValueChangeEventArguments], None],
) -> None:
    job = result.job
    text, _ = OUTCOME_TEXT[result.outcome]
    shade = OUTCOME_SHADE.get(result.outcome)
    band = f"background-color: {SHADE[shade]};" if shade else "border-bottom: 1px solid #E0E0E0;"
    with (
        ui.card()
        .props(f'flat bordered id="{anchor("results", job.job_id)}"')
        .classes("w-full p-0 gap-0 rounded-lg overflow-hidden")
    ):
        with ui.row().classes("w-full items-center justify-between no-wrap px-4 py-3").style(band):
            job_title(job, "flex-1 min-w-0")
            ui.button(
                "See on Jobs", icon="work", on_click=lambda: go_to_card("jobs", job.job_id)
            ).props("outline dense no-caps").classes("bg-white pl-1 pr-3").mark(
                f"see-jobs:{job.job_id}"
            )
            ui.button(icon="open_in_new", on_click=_open_url(job.url)).props(
                'outline round dense aria-label="Open posting"'
            ).classes("bg-white").mark("open-posting").tooltip("Open posting")
        with ui.row().classes(
            "w-full items-center gap-2 px-4 pt-3" + ("" if result.matches else " pb-3")
        ):
            if job.company_raw:
                _chip(job.company_raw, "business")
            if job.location_raw:
                _chip(job.location_raw, "place")
            if job.remote_type is not RemoteType.UNKNOWN:
                _chip(WORK_MODE_TEXT[job.remote_type], "apartment")
            if job.hours_per_week:
                _chip(f"{job.hours_per_week:g} h/week", "schedule")
            if pay := pay_text(job):
                _chip(pay, "payments")
            _chip(text, "fact_check", shade)
            contact_line(job)
        if result.matches:
            with ui.column().classes("w-full gap-3 p-4"):
                for view in result.matches:
                    _match_row(service, view, moved)
        if result.rejected:
            # The reviewer's rejections: hidden, still holding their slot in the round, and
            # brought back with Reset (user decision 2026-11-09: rejections are often
            # misclicks).
            with (
                ui.expansion(
                    f"Show rejected ({len(result.rejected)})",
                    icon="visibility_off",
                    value=rejected_open,
                    on_value_change=remember,
                )
                .classes("w-full px-4 pb-2 text-sm text-grey-8")
                .mark("show-rejected"),
                ui.column().classes("w-full gap-3 py-2"),
            ):
                for view in result.rejected:
                    _match_row(service, view, moved)


def results_page(service: AppService, native: bool) -> None:
    results = service.results()
    counts = {k: sum(r.outcome == k for r in results) for k in OUTCOME_TEXT}
    with ui.column().classes(f"{PAGE} gap-3"):
        ui.label("Results").classes("text-h5")
        ui.label(
            f"{counts['fits']} job(s) with clear fits · {counts['check_partial']} to check by "
            f"hand · {counts['no_match']} with no match · {counts['not_judged']} not judged yet"
        ).classes("text-sm text-grey-8")
        if not results:
            ui.label("No jobs yet. Add a jobs file and click Find matches.")
        for result in results:
            _job_card(service, result)
        focus = take_focus("results")
        if focus:
            scroll_to_card("results", focus)

        async def export() -> None:
            try:
                done = await run.io_bound(service.export)
            except RUN_ERRORS as exc:
                ui.notify(str(exc), type="negative")
                return
            if done is None:
                return
            ui.notify(f"Saved {done.path.name} ({done.rows} rows). It holds names: keep it local.")
            if native:
                open_in_file_manager(done.path)
            else:
                ui.download.file(done.path)

        with ui.row().classes("w-full justify-between py-4"):
            ui.button("Export to Excel", icon="download", on_click=export).props("outline")
            ui.button(
                "Add jobs / new search", icon="add", on_click=lambda: ui.navigate.to("/")
            ).props("color=primary")


# ---- screen: jobs ----

# (background, border) per state. Green: matched; red: analyzed, nobody fits (still open for
# later runs); yellow: waiting to be analyzed; grey: completed or unavailable (never sent).
JOB_STATE_STYLE: dict[JobState, tuple[str, str]] = {
    "matched": ("#c7ffd2", "#4CAF50"),
    "no_match": ("#ffcfcf", "#D32F2F"),
    "waiting": ("#fff385", "#F9A825"),
    "inactive": ("#EEEEEE", "#BDBDBD"),
}
JOB_STATE_TEXT: dict[JobState, str] = {
    "matched": "Scanned",
    "no_match": "Scanned",
    "waiting": "Not scanned yet",
    "inactive": "Closed",
}


# The application statuses, in the dropdown's order (user wording 2026-11-09).
APPLICATION_TEXT: dict[ApplicationStatus, str] = {
    ApplicationStatus.NOT_REFERRED: "Not referred yet",
    ApplicationStatus.OUTREACHED: "Outreached to HR",
    ApplicationStatus.IN_PROGRESS: "HR replied, in progress",
    ApplicationStatus.REJECTED: "Rejection / Ghosted",
    ApplicationStatus.HIRED: "Hired",
}


def _candidates_table(service: AppService, item: JobOverview, refresh: Callable[[], None]) -> None:
    """The approved candidates of one job: name, headline, score, the application status
    dropdown with Save; a rejected or ghosted application is greyed with Undo."""
    job_id = item.job.job_id
    if not item.candidates:
        ui.label("No approved candidates yet. Approve them on Results.").classes(
            "text-sm text-grey-7"
        )
        return

    def run_action(action: Callable[[], object], done: str) -> None:
        try:
            action()
        except RUN_ERRORS as exc:
            ui.notify(str(exc), type="warning")
            return
        ui.notify(done, type="positive")
        refresh()

    with ui.element("div").classes(
        "w-full grid grid-cols-[minmax(8rem,1fr)_minmax(8rem,2fr)_auto_minmax(13rem,auto)_auto] "
        "items-center gap-x-4 gap-y-2 text-sm"
    ):
        # A no-break space for the Save column's header: an empty label has no height, so its
        # underline sat higher than the others (user request 2026-11-10).
        for header in ("Candidate", "Headline", "Score", "Application status", "\u00a0"):
            ui.label(header).classes(
                "text-xs text-grey-7 uppercase border-b border-grey-4 pb-1 self-end"
            )
        for c in item.candidates:
            key = f"{job_id}:{c.talent_id}"
            dim = " opacity-50 line-through" if c.removed else ""
            ui.label(c.name or c.talent_id).classes("font-bold" + dim)
            ui.label(c.headline or "").classes("text-grey-8" + dim)
            score = c.match.fit_score if c.match and c.match.fit_score is not None else None
            ui.label("—" if score is None else str(score)).classes(dim)
            if c.removed:
                ui.label(APPLICATION_TEXT[ApplicationStatus.REJECTED]).classes("text-grey-7")
                ui.button(
                    "Undo",
                    icon="undo",
                    on_click=lambda t=c.talent_id: run_action(
                        lambda: service.undo_application_rejection(job_id, t), "Brought back."
                    ),
                ).props("outline dense no-caps").classes("bg-white").mark(f"undo:{key}")
                continue
            choice = (
                ui.select({s.value: text for s, text in APPLICATION_TEXT.items()}, value=c.status)
                .props("dense outlined bg-color=white")
                .classes("w-full")
                .mark(f"status:{key}")
            )

            def save(t: str = c.talent_id, box: ui.select = choice) -> None:
                status = ApplicationStatus(box.value)
                note = (
                    "Saved. The job is marked completed and the contractor not available."
                    if status is ApplicationStatus.HIRED
                    else "Saved."
                )
                run_action(lambda: service.set_application(job_id, t, status), note)

            ui.button("Save", icon="save", on_click=save).props("outline dense no-caps").classes(
                "bg-white"
            ).mark(f"save-status:{key}")


DANGER = "#C62828"  # red for destructive actions (Quasar's red-9)


def confirm_delete(title: str, lines: list[str], on_confirm: Callable[[], None]) -> None:
    """Every deletion asks first (user request 2026-11-10): a pop-up naming what goes,
    with Cancel as the safe default."""
    with ui.dialog() as dialog, ui.card().classes("max-w-lg"):
        ui.label(title).classes("text-h6")
        for line in lines:
            ui.label(line).classes("text-sm")
        ui.label("This cannot be undone from the app.").classes("text-sm font-medium")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat no-caps").mark("delete-cancel")

            def go() -> None:
                dialog.close()
                on_confirm()

            ui.button("Delete", icon="delete_forever", on_click=go).props(
                "unelevated no-caps text-color=white"
            ).props("color=red-9").mark("delete-confirm")
    dialog.open()


def danger_button(text: str, on_click: Callable[[], None], marker: str) -> ui.button:
    """A red button for a deletion; icon only when `text` is empty. The colour goes through
    Quasar's own `color` prop (a style alone can be overridden by the theme)."""
    button = ui.button(text or None, icon="delete_forever", on_click=on_click).props(
        "unelevated dense no-caps color=red-9 text-color=white"
    )
    if text:
        button.classes("pl-1 pr-3")
    else:
        button.props('aria-label="Delete"').classes("px-2")
    return button.mark(marker)


def _jobs_card(
    service: AppService, item: JobOverview, refresh: Callable[[], None], opened: set[str]
) -> None:
    job = item.job
    background, border = JOB_STATE_STYLE[item.state]
    grey = item.state == "inactive"

    def act(action: JobAction) -> Callable[[], None]:
        def handler() -> None:
            try:
                service.set_job_availability(job.job_id, action)
            except RUN_ERRORS as exc:
                ui.notify(str(exc), type="warning")
                return
            refresh()

        return handler

    def remember(e: ValueChangeEventArguments) -> None:
        (opened.add if e.value else opened.discard)(job.job_id)

    def delete() -> None:
        title = title_case(job.title_norm or job.title_raw)

        def go() -> None:
            try:
                kept = service.delete_job(job.job_id)
            except RUN_ERRORS as exc:
                ui.notify(str(exc), type="warning")
                return
            ui.notify(
                f"Deleted {title}."
                + (f" Still listed for {kept} contractor(s) on Talent." if kept else ""),
                type="positive",
            )
            refresh()

        confirm_delete(
            f"Delete {title}?",
            [
                "The job leaves the app: Jobs, Results and future matching. Its scans, rounds "
                "and application statuses are deleted.",
                "Contractors approved for it still see it on the Talent screen.",
            ],
            go,
        )

    card = (
        ui.card()
        .props(f'flat id="{anchor("jobs", job.job_id)}"')
        .classes("w-full px-4 py-3 gap-1 rounded-lg")
    )
    card.style(f"background-color: {background}; border: 2px solid {border};")
    card.mark("job-card", f"job-state-{item.state}")
    with card:
        _jobs_card_head(item, act, grey)
        active = [c for c in item.candidates if not c.removed]
        with ui.row().classes("w-full items-start no-wrap gap-2"):
            with (
                ui.expansion(
                    f"Approved candidates ({len(active)})",
                    icon="groups",
                    value=job.job_id in opened,
                    on_value_change=remember,
                )
                .props("dense")
                .classes("flex-1 min-w-0 text-sm")
                .mark(f"candidates:{job.job_id}")
            ):
                _candidates_table(service, item, refresh)
            # Away from Reopen (user request): the card's bottom-right corner, icon only.
            if grey:
                danger_button("", delete, f"delete-job:{job.job_id}").tooltip(
                    "Delete this closed job from the app"
                )


def _jobs_card_head(
    item: JobOverview,
    act: Callable[[JobAction], Callable[[], None]],
    grey: bool,
) -> None:
    job = item.job
    with ui.row().classes("w-full items-start justify-between no-wrap gap-3"):
        with ui.column().classes("flex-1 min-w-0 gap-0" + (" opacity-60" if grey else "")):
            job_title(job)
            where = " · ".join(p for p in (job.company_raw, job.location_raw) if p)
            if where:
                ui.label(where).classes("text-sm text-grey-8")
            terms = " · ".join(
                p
                for p in (
                    WORK_MODE_TEXT.get(job.remote_type),
                    f"{job.hours_per_week:g} h/week" if job.hours_per_week else None,
                    pay_text(job),
                )
                if p
            )
            if terms:
                ui.label(terms).classes("text-sm text-grey-8")
            contact_line(job)
            line = JOB_STATE_TEXT[item.state]
            if item.summary:
                line += f" · {item.summary}"
            ui.label(line).classes("text-sm font-medium pt-1").mark("job-summary")
        with ui.row().classes("items-center gap-2 no-wrap"):
            if not grey:  # closed jobs are not on Results
                ui.button(
                    "See on Results",
                    icon="fact_check",
                    on_click=lambda: go_to_card("results", job.job_id),
                ).props("outline dense no-caps").classes("bg-white pl-1 pr-3").mark(
                    f"see-results:{job.job_id}"
                )
            ui.button(icon="open_in_new", on_click=_open_url(job.url)).props(
                'outline round dense aria-label="Open posting"'
            ).classes("bg-white").tooltip("Open posting")
            toggles: list[tuple[str, str, JobAction, str]]
            if grey:
                toggles = [("Reopen", "replay", "reopen", "Available again for matching")]
            else:
                # No Completed button (user request 2026-11-10): a job is completed when an
                # application is set to Hired.
                toggles = [
                    ("Unavailable", "block", "unavailable", "The posting expired or is not valid"),
                ]
            for text, icon, action, tip in toggles:
                # Icon and text centred; extra room after the text (user request 2026-11-09).
                ui.button(text, icon=icon, on_click=act(action)).props(
                    "outline dense no-caps align=center"
                ).classes("bg-white items-center pl-1 pr-3").mark(f"{action}:{job.job_id}").tooltip(
                    tip
                )


def keep_order(jobs: list[JobOverview], order: list[str]) -> list[JobOverview]:
    """`jobs` in the order already on screen (job IDs in `order`); any job not shown yet
    goes after them, in the service's order. A toggle repaints the cards without moving them;
    the new order shows on the next visit or page refresh."""
    place = {job_id: i for i, job_id in enumerate(order)}
    return sorted(jobs, key=lambda o: place.get(o.job.job_id, len(place)))


def _fold(text: str) -> str:
    """Lower case without accents, so "quebec" finds "Québec" (French postings)."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def job_matches_search(job: Job, words: list[str]) -> bool:
    """Every search word appears in the job's title or company (user request 2026-11-10)."""
    text = _fold(" ".join(t for t in (job.title_norm, job.title_raw, job.company_raw) if t))
    return all(_fold(w) in text for w in words)


JOB_FILTERS = {
    "all": "All (A–Z)",
    "approved": "With approved candidates",
    "unavailable": "Unavailable",
}


def job_in_filter(item: JobOverview, name: str) -> bool:
    """The Jobs screen filters (user request 2026-11-10). "all" keeps the default order:
    open jobs A–Z, then closed ones A–Z."""
    if name == "approved":
        return any(not c.removed for c in item.candidates)
    if name == "unavailable":
        return item.job.availability is JobAvailability.UNAVAILABLE
    return True


def jobs_page(service: AppService, native: bool) -> None:
    shown: list[str] = []  # job IDs in on-screen order, fixed when the page opens
    opened: set[str] = set()  # cards whose candidates table is open (kept across repaints)
    query = {"text": "", "filter": "all"}
    focus = take_focus("jobs")  # opened from a Results card: its candidates table open
    if focus:
        opened.add(focus)

    @ui.refreshable
    def body() -> None:
        jobs = keep_order(service.jobs_overview(), shown)
        shown[:] = [o.job.job_id for o in jobs]
        counts = {s: sum(j.state == s for j in jobs) for s in JOB_STATE_STYLE}
        scanned = counts["matched"] + counts["no_match"]
        ui.label(
            f"{len(jobs)} job(s) · {scanned} scanned ({counts['matched']} with matches, "
            f"{counts['no_match']} with no match) · {counts['waiting']} not scanned yet · "
            f"{counts['inactive']} closed"
        ).classes("text-sm text-grey-8")
        words = query["text"].split()
        matching = [
            o
            for o in jobs
            if job_matches_search(o.job, words) and job_in_filter(o, query["filter"])
        ]
        if not jobs:
            ui.label("No jobs yet. Add a jobs file and click Find matches.")
        elif not matching:
            ui.label("No job matches the search.").classes("text-grey-7")
        for item in matching:
            _jobs_card(service, item, body.refresh, opened)

    def search(e: ValueChangeEventArguments) -> None:
        query["text"] = e.value or ""
        body.refresh()

    def pick_filter(name: str) -> None:
        query["filter"] = name
        filter_button.text = "" if name == "all" else JOB_FILTERS[name]
        filter_button.props(f"color={'primary' if name != 'all' else 'grey-8'}")
        body.refresh()

    with ui.column().classes(f"{PAGE} gap-3"):
        ui.label("Jobs").classes("text-h5")
        ui.label(
            "A job is completed when an approved candidate is set to Hired; mark it "
            "Unavailable to stop matching it. Reopen puts it back; the next Find matches sends "
            "only contractors not yet judged for it. A closed job can be deleted."
        ).classes("text-xs text-grey-7")
        with ui.row().classes("w-full items-center gap-3"):
            ui.input(placeholder="Search by job title or company", on_change=search).props(
                "dense outlined clearable bg-color=white"
            ).classes("w-full max-w-md").mark("jobs-search")
            # A filter icon with a dropdown (user request 2026-11-10); it names the filter
            # while one other than "All" is on.
            with (
                ui.button(icon="filter_list")
                .props("outline dense no-caps icon-right=arrow_drop_down color=grey-8")
                .classes("bg-white pl-2")
                .mark("jobs-filter")
                .tooltip("Filter jobs") as filter_button,
                ui.menu(),
            ):
                for name, text in JOB_FILTERS.items():
                    ui.menu_item(text, on_click=lambda n=name: pick_filter(n)).mark(
                        f"jobs-filter:{name}"
                    )
        body()
        if focus:
            scroll_to_card("jobs", focus)


# ---- screen: talent ----

# By available. Unavailable: a clearly grey card, distinct from the light purple page
# (#EEEEEE was barely visible against it; user request).
TALENT_STYLE = {True: ("#FFFFFF", "#D1C4E9"), False: ("#D6D6D6", "#9E9E9E")}


def _talent_jobs_table(item: TalentOverview) -> None:
    """The jobs a contractor was approved for, with the status set on the Jobs screen
    (read-only here). Rejected / ghosted applications and closed jobs are greyed."""
    if not item.jobs:
        ui.label("Not approved for any job yet.").classes("text-sm text-grey-7")
        return
    with ui.element("div").classes(
        "w-full grid grid-cols-[minmax(10rem,2fr)_minmax(8rem,1fr)_minmax(8rem,1fr)_auto] "
        "items-center gap-x-4 gap-y-2 text-sm"
    ):
        for header in ("Job title", "Company", "Location", "Application status"):
            ui.label(header).classes("text-xs text-grey-7 uppercase border-b border-grey-4 pb-1")
        for job in item.jobs:
            dim = " text-grey-6" if job.removed or not job.job_open else ""
            closed = " (removed)" if job.deleted else "" if job.job_open else " (closed)"
            title = title_case(job.title) + closed
            ui.label(title).classes("font-medium" + dim)
            ui.label(job.company or "—").classes(dim)
            ui.label(job.location or "—").classes(dim)
            ui.label(APPLICATION_TEXT[job.status]).classes(dim).mark(
                f"talent-status:{item.talent_id}:{job.job_id}"
            )


def _talent_card(
    service: AppService, item: TalentOverview, refresh: Callable[[], None], opened: set[str]
) -> None:
    background, border = TALENT_STYLE[item.available]
    card = ui.card().props("flat").classes("w-full px-4 py-3 gap-1 rounded-lg")
    card.style(f"background-color: {background}; border: 2px solid {border};")
    card.mark("talent-card", f"talent-{'available' if item.available else 'unavailable'}")

    def save() -> None:
        try:
            service.set_talent_availability(item.talent_id, bool(switch.value), note.value or "")
        except RUN_ERRORS as exc:
            ui.notify(str(exc), type="warning")
            return
        ui.notify("Saved.", type="positive")
        refresh()

    def remember(e: ValueChangeEventArguments) -> None:
        (opened.add if e.value else opened.discard)(item.talent_id)

    def delete() -> None:
        def go() -> None:
            try:
                service.delete_talent(item.talent_id)
            except RUN_ERRORS as exc:
                ui.notify(str(exc), type="warning")
                return
            ui.notify(f"Deleted {item.name}.", type="positive")
            refresh()

        confirm_delete(
            f"Delete {item.name}?",
            [
                "Everything about them leaves the app: profile, résumé, availability, "
                "recommendations, your decisions on them and their application statuses.",
                "Your contractor folder is not touched: importing it again adds them back.",
            ],
            go,
        )

    with card:
        with ui.row().classes("w-full items-stretch no-wrap gap-4"):
            # The availability switch, in its own section on the left (user request).
            with ui.column().classes(
                "items-center justify-center gap-0 pr-2 border-r border-grey-4 w-20 shrink-0"
            ):
                switch = ui.switch(value=item.available).mark(f"available:{item.talent_id}")
                ui.label("Available").classes("text-xs text-grey-8 text-center leading-tight")
            with ui.column().classes("flex-1 min-w-0 gap-1"):
                with ui.row().classes("items-center gap-2"):
                    ui.label(item.name).classes("text-h6")
                    if not item.available:
                        ui.label("Not available for matching").classes(
                            "text-xs px-2 rounded bg-grey-4 text-grey-9"
                        )
                    # The contractor's own answer, when the reviewer's toggle disagrees.
                    if item.toggled is not None and item.toggled.available != item.profile_open:
                        said = "open" if item.profile_open else "not open"
                        ui.label(f"Profile says: {said} to contract work").classes(
                            "text-xs px-2 rounded bg-yellow-2 text-grey-9"
                        ).mark("profile-disagrees")
                    elif item.toggled is None and not item.profile_open:
                        ui.label("Profile says: not open to contract work").classes(
                            "text-xs px-2 rounded bg-grey-3 text-grey-9"
                        )
                if item.headline:
                    ui.label(item.headline).classes("text-grey-8")
                if item.toggled is not None:
                    t = item.toggled
                    state = "available" if t.available else "not available"
                    line = f"Set {state} by {t.reviewer} on {t.changed_at:%Y-%m-%d}"
                    ui.label(line + (f": {t.note}" if t.note else "")).classes(
                        "text-xs text-grey-7"
                    ).mark("talent-note")
            # Right section: Profile, Résumé and Save on the first row, the note under them
            # (user request).
            with ui.column().classes("gap-2 self-start items-stretch shrink-0"):
                with ui.row().classes("items-center justify-end gap-2 no-wrap"):
                    ui.button(
                        "Profile",
                        icon="badge",
                        on_click=lambda: _profile_dialog(service, item.talent_id, item.name),
                    ).props("outline dense no-caps").classes("bg-white pl-1 pr-3")
                    # Always shown; greyed out when there is no résumé on file (user request).
                    # The tooltip sits on a wrapper: a disabled button gets no hover events.
                    with ui.element("span") as wrapper:
                        resume = (
                            ui.button(
                                "Résumé",
                                icon="picture_as_pdf",
                                on_click=lambda: _resume_dialog(item.talent_id, item.name),
                            )
                            .props("outline dense no-caps")
                            .classes("bg-white pl-1 pr-3")
                            .mark(f"resume:{item.talent_id}")
                        )
                    if not item.has_resume:
                        resume.disable()
                        resume.props("color=grey-7")
                        wrapper.tooltip("No résumé on file")
                    # Icon only, the same height as Profile and Résumé (user request).
                    ui.button(icon="save", on_click=save).props(
                        'outline dense aria-label="Save availability"'
                    ).classes("bg-white px-2").mark(f"save-available:{item.talent_id}").tooltip(
                        "Save availability"
                    )
                note = (
                    ui.input(placeholder="Note (optional)")
                    .props("dense outlined bg-color=white")
                    .classes("w-full")
                    .mark(f"note:{item.talent_id}")
                )
        live = [j for j in item.jobs if not j.removed]
        with ui.row().classes("w-full items-start no-wrap gap-2"):
            with (
                ui.expansion(
                    f"Approved for jobs ({len(live)})",
                    icon="work",
                    value=item.talent_id in opened,
                    on_value_change=remember,
                )
                .props("dense")
                .classes("flex-1 min-w-0 text-sm talent-jobs")
                .mark(f"talent-jobs:{item.talent_id}")
            ):
                _talent_jobs_table(item)
            # Away from Save (user request): the card's bottom-right corner, icon only.
            if not item.available:
                danger_button("", delete, f"delete-talent:{item.talent_id}").tooltip(
                    "Delete this contractor and all their data"
                )


def talent_page(service: AppService, native: bool) -> None:
    shown: list[str] = []  # talent IDs in on-screen order, fixed when the page opens
    opened: set[str] = set()
    query = {"text": ""}

    @ui.refreshable
    def body() -> None:
        everyone = service.talent_overview()
        place = {talent_id: i for i, talent_id in enumerate(shown)}
        everyone.sort(key=lambda t: place.get(t.talent_id, len(place)))  # no jumping
        shown[:] = [t.talent_id for t in everyone]
        available = sum(t.available for t in everyone)
        ui.label(
            f"{len(everyone)} contractor(s) · {available} available for matching · "
            f"{len(everyone) - available} not available"
        ).classes("text-sm text-grey-8")
        words = query["text"].casefold().split()
        matching = [
            t
            for t in everyone
            if all(w in f"{t.name} {t.headline or ''}".casefold() for w in words)
        ]
        if not everyone:
            ui.label("No contractors yet. Pick the contractor list on Find matches.")
        elif not matching:
            ui.label("Nobody matches the search.").classes("text-grey-7")
        for item in matching:
            _talent_card(service, item, body.refresh, opened)

    def search(e: ValueChangeEventArguments) -> None:
        query["text"] = e.value or ""
        body.refresh()

    # A smaller "work" icon on the "Approved for jobs" caret (user request 2026-11-09).
    ui.add_css(
        ".talent-jobs .q-item__section--avatar { min-width: 26px; }"
        ".talent-jobs .q-item__section--avatar .q-icon { font-size: 16px; }"
    )
    with ui.column().classes(f"{PAGE} gap-3"):
        ui.label("Talent").classes("text-h5")
        ui.label(
            "Turn off Available for matching when a contractor isn't looking for a project "
            "(check with them first): they are no longer sent to Gemini, and their open "
            "recommendations make room for others. A hire never changes it. Profile changes go "
            "through the contractor intake form and the next contractor list import; a new or "
            "changed profile in an import sets it from the contractor's own answer."
        ).classes("text-xs text-grey-7")
        ui.input(placeholder="Search by name or headline", on_change=search).props(
            "dense outlined clearable bg-color=white"
        ).classes("w-full max-w-md").mark("talent-search")
        body()


# ---- screen: settings ----


def settings_page(service: AppService, native: bool) -> None:
    with ui.column().classes(f"{PAGE_NARROW} gap-4"):
        ui.label("Settings").classes("text-h5")
        with ui.card().classes("w-full"):
            ui.label("Gemini key").classes("text-h6")
            state = ui.label(
                "A key is saved." if service.has_key() else "No key saved yet."
            ).classes("text-sm")
            ui.label(
                "Free key from Google AI Studio. It stays on this computer and is never shown."
            ).classes("text-xs text-grey-7")
            key = (
                ui.input("Paste a new key", password=True, password_toggle_button=True)
                .classes("w-full")
                .mark("key-input")
            )

            def save_key() -> None:
                try:
                    service.save_key(key.value or "")
                except AppError as exc:
                    ui.notify(str(exc), type="warning")
                    return
                key.value = ""
                state.text = "A key is saved."
                ui.notify("Key saved.", type="positive")

            ui.button("Save key", on_click=save_key)

        with ui.card().classes("w-full"):
            ui.label("Your name").classes("text-h6")
            ui.label("Stored with each approve / reject decision.").classes("text-xs text-grey-7")
            name = ui.input("Reviewer", value=service.reviewer()).classes("w-full")

            def save_name() -> None:
                try:
                    service.save_reviewer(name.value or "")
                except AppError as exc:
                    ui.notify(str(exc), type="warning")
                    return
                ui.notify("Saved.", type="positive")

            ui.button("Save name", on_click=save_name)

        with ui.card().classes("w-full"):
            ui.label("Data folder").classes("text-h6")
            ui.label(str(service.data_dir)).classes("text-sm font-mono")
            ui.label(
                "Jobs, contractors, results and exports live here. Contractor data is personal: "
                "keep this computer password-protected."
            ).classes("text-xs text-grey-7")
            if native:
                ui.button(
                    "Show in folder", on_click=lambda: open_in_file_manager(service.data_dir)
                ).props("outline")

        backup_card(service, native)


# ---- export and import of all data (user decisions 2026-11-10) ----


def _size(n: int) -> str:
    return f"{n / 1024**2:.1f} MB" if n >= 1024**2 else f"{max(n, 1) / 1024:.0f} KB"


def backup_card(service: AppService, native: bool) -> None:
    with ui.card().classes("w-full gap-2"):
        ui.label("Back up or move the app").classes("text-h6")
        ui.label(
            "Export saves all jobs, contractors, résumés, decisions and Gemini's saved answers "
            "in one password-protected file. Import replaces this app's data with such a file, "
            "for a new computer, a new person or after a reset. The Gemini key and the reviewer "
            "name are never in the file: enter them here after an import."
        ).classes("text-sm text-grey-8")
        with ui.row().classes("gap-2"):
            ui.button(
                "Export all data", icon="archive", on_click=lambda: export_dialog(service, native)
            ).props("outline no-caps").mark("export-data")
            ui.button(
                "Import a backup", icon="unarchive", on_click=lambda: import_dialog(service, native)
            ).props("outline no-caps").mark("import-data")


def export_dialog(service: AppService, native: bool) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-[480px] max-w-full gap-2"):
        ui.label("Export all data").classes("text-h6")
        ui.label(
            "Choose a password for the file. Without it the backup cannot be opened, and it "
            "cannot be recovered: keep it somewhere safe, apart from the file."
        ).classes("text-sm text-grey-8")
        first = (
            ui.input("Password", password=True, password_toggle_button=True)
            .props('outlined dense hint="At least 10 characters."')
            .classes("w-full")
            .mark("export-password")
        )
        second = (
            ui.input("Password again", password=True)
            .props("outlined dense")
            .classes("w-full")
            .mark("export-password-again")
        )

        async def go() -> None:
            if first.value != second.value:
                ui.notify("The two passwords differ.", type="warning")
                return
            dest: Path | None = None
            if native:
                import webview  # only in the desktop app

                window = nicegui_app.native.main_window
                if window is None:
                    ui.notify("The save dialog is not available.", type="negative")
                    return
                chosen = await window.create_file_dialog(
                    webview.FileDialog.SAVE, save_filename=backup_name(datetime.now(UTC))
                )
                if not chosen:
                    return
                dest = Path(chosen if isinstance(chosen, str) else chosen[0])
            try:
                info, path = await run.io_bound(service.export_data, str(first.value or ""), dest)
            except AppError as exc:
                ui.notify(str(exc), type="warning", multi_line=True)
                return
            dialog.close()
            ui.notify(
                f"Exported {info.jobs} job(s) and {info.contractors} contractor(s) "
                f"({_size(info.size)}).",
                type="positive",
            )
            if not native:
                ui.download(path, path.name)

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
            ui.button("Export", icon="archive", on_click=go).props("no-caps").mark("export-confirm")
    dialog.open()


def import_dialog(service: AppService, native: bool) -> None:
    picked: dict[str, Path] = {}
    with ui.dialog() as dialog, ui.card().classes("w-[520px] max-w-full gap-2"):
        ui.label("Import a backup").classes("text-h6")
        ui.label(
            "Pick a backup file made with Export, and type its password. It is checked "
            "completely before anything changes."
        ).classes("text-sm text-grey-8")
        chooser, note = pick_row("Choose backup…", "unarchive", "import-file")
        password = (
            ui.input("Password", password=True, password_toggle_button=True)
            .props("outlined dense")
            .classes("w-full")
            .mark("import-password")
        )

        if native:

            async def browse() -> None:
                window = nicegui_app.native.main_window
                if window is None:
                    ui.notify("The file dialog is not available.", type="negative")
                    return
                chosen = await window.create_file_dialog(file_types=("Backup (*.zip)",))
                if chosen:
                    picked["path"] = Path(chosen[0])
                    note.text = str(picked["path"])

            chooser.on_click(browse)
        else:

            async def uploaded(e: UploadEventArguments) -> None:
                dest = service.data_dir / "backups" / "uploads" / e.file.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(await e.file.read())
                picked["path"] = dest
                note.text = e.file.name

            upload = (
                ui.upload(on_upload=uploaded, auto_upload=True)
                .props('accept=".zip"')
                .classes("hidden")
                .mark("import-upload")
            )
            chooser.on_click(lambda: upload.run_method("pickFiles"))

        async def check() -> None:
            path = picked.get("path")
            if path is None:
                ui.notify("Choose a backup file first.", type="warning")
                return
            try:
                info = await run.io_bound(service.check_import, path, str(password.value or ""))
            except AppError as exc:
                ui.notify(str(exc), type="warning", multi_line=True)
                return
            dialog.close()
            confirm_import(service, info)

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
            ui.button("Check backup", icon="fact_check", on_click=check).props("no-caps").mark(
                "import-check"
            )
    dialog.open()


def confirm_import(service: AppService, info: BackupInfo) -> None:
    jobs, contractors = service.data_counts()
    with ui.dialog().props("persistent") as dialog, ui.card().classes("max-w-lg gap-2"):
        ui.label("Replace this app's data?").classes("text-h6")
        ui.label(
            f"Now: {jobs} job(s) and {contractors} contractor(s). The backup from "
            f"{info.created_at:%Y-%m-%d %H:%M} UTC (app {info.app_version}): {info.jobs} job(s), "
            f"{info.contractors} contractor(s), {info.files} file(s), {_size(info.size)}."
        ).classes("text-sm")
        ui.label(
            "The backup passed every check. Your current data is not deleted: it is moved to "
            "the backups folder (data/backups/before-import-…), so the import can be undone."
        ).classes("text-sm text-grey-8")

        def cancel() -> None:
            service.discard_import()
            dialog.close()

        async def go() -> None:
            try:
                kept = await run.io_bound(service.import_checked)
            except AppError as exc:
                ui.notify(str(exc), type="negative", multi_line=True)
                return
            dialog.close()
            ui.notify(
                f"Imported. The previous data is kept in {kept.name}.",
                type="positive",
                multi_line=True,
            )
            ui.navigate.to("/settings")

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=cancel).props("flat no-caps").mark("import-cancel")
            ui.button("Replace data", icon="unarchive", on_click=go).props(
                "unelevated no-caps color=red-9 text-color=white"
            ).mark("import-confirm")
    dialog.open()


# ---- the app ----


def _go(path: str) -> Callable[[], None]:
    return lambda: ui.navigate.to(path)


def build_root(service: AppService, *, native: bool) -> Callable[[], None]:
    def root() -> None:
        _serve_resumes(service)  # here, not once at start: the test harness resets routes
        ui.page_title(APP_TITLE)
        ui.colors(primary=THEME_PRIMARY)
        ui.query("body").style(f"background-color: {THEME_BACKGROUND}")
        with ui.header().classes("items-center"):
            ui.label(APP_TITLE).classes("text-lg font-bold").mark("app-title")
            ui.space()
            for text, path in (
                ("Find matches", "/"),
                ("Results", "/results"),
                ("Jobs", "/jobs"),
                ("Talent", "/talent"),
                ("Settings", "/settings"),
            ):
                ui.button(text, on_click=_go(path)).props("flat color=white")
        # NiceGUI's page wrapper is a left-aligned column sized to its content: make it full
        # width and centre each screen in it (the header is separate and keeps full width).
        ui.sub_pages(
            {
                "/": lambda: home_page(service, native),
                "/results": lambda: results_page(service, native),
                "/jobs": lambda: jobs_page(service, native),
                "/talent": lambda: talent_page(service, native),
                "/settings": lambda: settings_page(service, native),
            }
        ).classes("w-full items-center")

    return root
