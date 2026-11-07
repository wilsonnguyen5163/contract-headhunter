"""The desktop app's screens (NiceGUI). All logic lives in app/service.py; this file only
lays out the screens and wires buttons to it.

Screens:
- "/"         Find matches: pick a contractor file and a jobs file, run, watch progress.
- "/results"  One card per job: up to 3 fits, or the best partial to check, or no match;
              approve / reject / needs info on each; export to Excel; start a new search.
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
from collections.abc import Awaitable, Callable
from pathlib import Path

from nicegui import app as nicegui_app
from nicegui import run, ui
from nicegui.events import UploadEventArguments

from jobmatch.app.service import (
    CONTRACTOR_SUFFIXES,
    JOB_SUFFIXES,
    AppError,
    AppService,
    JobResult,
    MatchView,
)
from jobmatch.llm.usage import RunCheck
from jobmatch.pipeline import PipelineError
from jobmatch.schemas import Decision, ReasonCode
from jobmatch.sources.contractor_upsert import ContractorFileError
from jobmatch.storage.csv_store import CsvStoreError
from jobmatch.storage.loader import StorageBackendUnavailable

# Screen width follows the window: full width, side padding growing with the window, capped
# at 1600 px so lines stay readable on very wide monitors. Settings is a short form: narrower.
PAGE = "w-full mx-auto max-w-[1600px] px-2 sm:px-4 lg:px-8"
PAGE_NARROW = "w-full mx-auto max-w-[900px] px-2 sm:px-4 lg:px-8"

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


def file_picker(
    native: bool,
    suffixes: tuple[str, ...],
    label: str,
    on_file: Callable[[str, bytes, Path | None], Awaitable[None] | None],
) -> None:
    async def handle(name: str, data: bytes, path: Path | None) -> None:
        result = on_file(name, data, path)
        if asyncio.iscoroutine(result):
            await result

    if native:

        async def browse() -> None:
            window = nicegui_app.native.main_window
            if window is None:
                ui.notify("The file dialog is not available.", type="negative")
                return
            patterns = ";".join(f"*{s}" for s in suffixes)
            picked = await window.create_file_dialog(file_types=(f"{label} ({patterns})",))
            if picked:
                path = Path(picked[0])
                await handle(path.name, path.read_bytes(), path)

        ui.button("Browse…", icon="folder_open", on_click=browse).props("outline")
    else:

        async def uploaded(e: UploadEventArguments) -> None:
            await handle(e.file.name, await e.file.read(), None)

        ui.upload(label=f"Choose {label.lower()}", on_upload=uploaded, auto_upload=True).props(
            f'accept="{",".join(suffixes)}" flat bordered'
        ).classes("w-full")


# ---- screen: find matches ----


def home_page(service: AppService, native: bool) -> None:
    with ui.column().classes(f"{PAGE} gap-4") as page:
        if not service.has_key():
            with ui.card().classes("w-full bg-amber-50"):
                ui.label("Add your Gemini key in Settings before the first run.")
                ui.button("Open Settings", on_click=lambda: ui.navigate.to("/settings")).props(
                    "flat"
                )

        with ui.card().classes("w-full"):
            ui.label("1. Contractors (optional)").classes("text-h6")
            ui.label(
                f"{service.contractor_count()} contractor(s) in the app. Pick the contractor "
                "file only when it has new or changed people: they are added or updated."
            ).classes("text-sm text-grey-8")
            picked = ui.label("No contractor file picked.").classes("text-sm")

            def contractor(name: str, data: bytes, path: Path | None) -> None:
                try:
                    info = service.pick_contractor_file(name, data=data, path=path)
                except AppError as exc:
                    ui.notify(str(exc), type="negative", multi_line=True)
                    return
                picked.text = (
                    f"{name}: {info.contractors} contractor(s), loaded when you click Find matches."
                )

            file_picker(native, CONTRACTOR_SUFFIXES, "Contractor file", contractor)

        with ui.card().classes("w-full"):
            ui.label("2. Jobs").classes("text-h6")
            ui.label(
                "A .txt file with one job link per line, or one posting saved as .txt (from the "
                "template), .html or .pdf. Jobs already imported are skipped."
            ).classes("text-sm text-grey-8")
            added = ui.column().classes("gap-0")

            def jobs(name: str, data: bytes, path: Path | None) -> None:
                try:
                    info = service.add_jobs_file(name, data)
                except AppError as exc:
                    ui.notify(str(exc), type="negative")
                    return
                what = f"{info.links} link(s)" if info.kind == "links" else "a posting"
                with added:
                    ui.label(f"Added {name}: {what}.").classes("text-sm")

            file_picker(native, JOB_SUFFIXES, "Jobs file", jobs)

        with ui.column().classes("w-full items-center gap-2 py-2"):
            run_button = ui.button("Find matches", icon="search").props("size=lg color=primary")
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

    async def start() -> None:
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
        after.clear()
        bar.value = 0
        bar.set_visibility(True)
        status.text = "Working… this can take a few minutes when there are new jobs."
        timer = ui.timer(0.2, drain)
        try:
            report = await run.io_bound(
                service.run, echo=lines.put, confirm=confirm, progress=on_progress
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
            q = service.quota()
            quota_label.text = (
                f"Gemini today: {q.requests} of {q.rpd} requests, {q.tokens:,} tokens."
            )
        if report is None:
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

    run_button.on_click(start)


# ---- screen: results ----

# Light shades, half way between white and the full colour (Quasar positive / warning /
# negative), so colour codes stay readable behind dark text.
SHADE = {"green": "#90DCA2", "yellow": "#F8DF9B", "red": "#E07F8A"}
# Two steps lighter (about 75% toward white), for the Approve / Reject / Needs info buttons.
SHADE_LIGHT = {"green": "#C8EED0", "yellow": "#FCEFCD", "red": "#F0BFC4"}
TEXT_DARK = "#1D1D1D"
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


def _profile_dialog(service: AppService, view: MatchView) -> None:
    try:
        fields = service.contractor_fields(view.match.talent_id)
    except AppError as exc:
        ui.notify(str(exc), type="warning")
        return
    with (
        ui.dialog() as dialog,
        ui.card().style("width: 90vw; max-width: 90vw; max-height: 80vh"),
    ):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(f"Profile: {view.name or view.match.talent_id}").classes("text-h6")
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


def _match_row(service: AppService, view: MatchView) -> None:
    m = view.match
    state = {"review": view.decision}

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
                "reset-decision"
            ).tooltip("Reset the decision")

    def save(decision: Decision, reason: ReasonCode | None = None, notes: str = "") -> None:
        try:
            state["review"] = service.decide(m.match_id, decision, reason, notes)
        except (AppError, CsvStoreError) as exc:
            ui.notify(str(exc), type="negative")
            return
        judgement.refresh()
        paint()

    def reset() -> None:
        try:
            service.clear_decision(m.match_id)
        except CsvStoreError as exc:
            ui.notify(str(exc), type="negative")
            return
        state["review"] = None
        judgement.refresh()
        paint()

    async def reject() -> None:
        with ui.dialog() as dialog, ui.card():
            ui.label("Why reject this match?").classes("text-h6")
            reason = ui.select(
                {code.value: text for code, text in REASON_TEXT.items()}, label="Reason"
            ).classes("w-64")
            notes = ui.input("Notes (optional)").classes("w-64")
            with ui.row():
                ui.button("Reject", on_click=lambda: dialog.submit(True)).props("color=negative")
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
                ui.label("fit" if fit else "partial").classes("text-xs px-2 rounded").style(
                    f"background-color: {SHADE['green' if fit else 'yellow']}; color: {TEXT_DARK};"
                )
                if m.fit_score is not None:
                    ui.label(f"score {m.fit_score}").classes("text-xs text-grey-8")
                if not m.detailed:
                    ui.label("reason only").classes("text-xs text-grey-7 italic")
            judgement()
        if m.reason:
            ui.label(m.reason).classes("text-sm")
        with ui.expansion("Details").classes("w-full text-sm"):
            with ui.row().classes("gap-2 pb-2"):
                ui.button(
                    "Profile", icon="badge", on_click=lambda: _profile_dialog(service, view)
                ).props("outline dense no-caps")
                ui.button("Resume", icon="description").props(
                    "outline dense no-caps disable"
                ).tooltip("Resumes come in a later version")
            for title, items in (
                ("Meets", m.requirements_met),
                ("Missing", m.requirements_missing),
                ("Flags", m.red_flags),
                ("Evidence (checked word for word)", m.evidence),
            ):
                if items:
                    ui.label(title).classes("font-bold")
                    for item in items:
                        ui.label(f"• {item}")
            if m.unverified_evidence:
                ui.label("A quote could not be verified and was removed.").classes("text-negative")
            if not m.detailed:
                ui.label(
                    "Gemini judged this contractor without describing them: only the reason "
                    "above is known."
                ).classes("text-grey-7")
        with ui.row().classes("gap-2 flex-wrap"):
            _shade_button("Approve", "green", lambda: save(Decision.APPROVED), icon="check")
            _shade_button("Reject", "red", reject, icon="close")
            _shade_button(
                "Needs info", "yellow", lambda: save(Decision.NEEDS_INFO), icon="help_outline"
            )


def _open_url(url: str) -> Callable[[], None]:
    return lambda: ui.navigate.to(url, new_tab=True)


def _chip(text: str, icon: str, shade: str | None = None) -> None:
    style = f"background-color: {SHADE[shade]};" if shade else "background-color: #F5F5F5;"
    ui.chip(text, icon=icon, color=None, text_color=None).props("dense square").classes(
        "m-0 text-sm"
    ).style(f"{style} color: {TEXT_DARK};")


def _job_card(service: AppService, result: JobResult) -> None:
    job = result.job
    text, _ = OUTCOME_TEXT[result.outcome]
    shade = OUTCOME_SHADE.get(result.outcome)
    band = f"background-color: {SHADE[shade]};" if shade else "border-bottom: 1px solid #E0E0E0;"
    with ui.card().props("flat bordered").classes("w-full p-0 gap-0 rounded-lg overflow-hidden"):
        with ui.row().classes("w-full items-center justify-between no-wrap px-4 py-3").style(band):
            ui.label(title_case(job.title_norm or job.title_raw)).classes("text-h6 flex-1 min-w-0")
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
            _chip(text, "fact_check", shade)
        if result.matches:
            with ui.column().classes("w-full gap-3 p-4"):
                for view in result.matches:
                    _match_row(service, view)


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


# ---- the app ----


def _go(path: str) -> Callable[[], None]:
    return lambda: ui.navigate.to(path)


def build_root(service: AppService, *, native: bool) -> Callable[[], None]:
    def root() -> None:
        ui.page_title("jobmatch")
        with ui.header().classes("items-center"):
            ui.label("jobmatch").classes("text-lg font-bold")
            ui.space()
            for text, path in (
                ("Find matches", "/"),
                ("Results", "/results"),
                ("Settings", "/settings"),
            ):
                ui.button(text, on_click=_go(path)).props("flat color=white")
        # NiceGUI's page wrapper is a left-aligned column sized to its content: make it full
        # width and centre each screen in it (the header is separate and keeps full width).
        ui.sub_pages(
            {
                "/": lambda: home_page(service, native),
                "/results": lambda: results_page(service, native),
                "/settings": lambda: settings_page(service, native),
            }
        ).classes("w-full items-center")

    return root
