"""The desktop app's screens, driven by NiceGUI's simulated user (no browser, no window).
Fake Gemini, no network. Each test runs its own event loop (no pytest-asyncio needed)."""

import asyncio
from collections.abc import Callable
from pathlib import Path

from nicegui import ui
from nicegui.testing.user_simulation import user_simulation

from jobmatch.app.service import AppService
from jobmatch.app.ui import SCORE_SHADE, SHADE, build_root, score_shade, title_case
from jobmatch.schemas import Decision, Job
from jobmatch.secrets_file import read_env_file
from jobmatch.storage.csv_store import CsvReviewRepository, CsvStore
from test_app_service import FAKE_KEY
from test_pipeline import add_jobs, contractor_file


def run_ui(service: AppService, scenario: Callable) -> None:
    async def main() -> None:
        async with user_simulation(root=build_root(service, native=False)) as user:
            await scenario(user)

    asyncio.run(main())


def test_home_asks_for_a_key_first(service: AppService) -> None:
    async def scenario(user) -> None:  # noqa: ANN001
        await user.open("/")
        await user.should_see("do good talent", marker="app-title")  # the header's title
        await user.should_see("Add your Gemini key in Settings")
        await user.should_see("Find matches")
        await user.should_see("0 contractor(s) in the app")

    run_ui(service, scenario)


def test_settings_saves_the_key_without_showing_it(service: AppService) -> None:
    async def scenario(user) -> None:  # noqa: ANN001
        await user.open("/settings")
        await user.should_see("No key saved yet.")
        user.find(marker="key-input").type(FAKE_KEY)
        user.find("Save key").click()
        await user.should_see("A key is saved.")
        await user.should_not_see(FAKE_KEY)

    run_ui(service, scenario)
    assert read_env_file(service.env_path)["GEMINI_API_KEY"] == FAKE_KEY


def test_results_show_fits_and_store_a_decision(
    service: AppService, store: CsvStore, make_job: Callable[..., Job], tmp_path: Path
) -> None:
    add_jobs(store, make_job)
    path = contractor_file(tmp_path)
    service.pick_contractor_file(path.name, path=path)
    service.run(echo=lambda s: None, confirm=lambda c: False)

    async def scenario(user) -> None:  # noqa: ANN001
        await user.open("/results")
        await user.should_see("2 job(s) with clear fits")
        await user.should_see("Alex Martin")
        await user.should_see("No match: nobody in the contractor list fits")  # result chip
        await user.should_not_see("Approved")  # no decision yet, so no tag and no Reset
        # The first box on screen: the one whose "Approve" is clicked below (the test user
        # clicks the lowest element id; find() returns a set, so pick by id the same way).
        box = min(user.find(marker="contractor-box").elements, key=lambda e: e.id)
        assert box.style["border-left"].startswith("1px")  # undecided: no colour
        user.find(kind=ui.button, content="Approve").click()
        await user.should_see("Approved")
        assert box.style["border-left"] == "4px solid #90DCA2"  # green: approved
        user.find(marker="reset-decision").click()
        await user.should_not_see("Approved")
        assert box.style["border-left"].startswith("1px")
        await user.should_see("Add jobs / new search")

    run_ui(service, scenario)
    reviews = CsvReviewRepository(store).list_all()
    assert [r.decision for r in reviews] == [Decision.APPROVED, Decision.CLEARED]


def test_profile_pop_up_shows_the_csv_row(
    service: AppService, store: CsvStore, make_job: Callable[..., Job], tmp_path: Path
) -> None:
    add_jobs(store, make_job)
    path = contractor_file(tmp_path)
    service.pick_contractor_file(path.name, path=path)
    service.run(echo=lambda s: None, confirm=lambda c: False)

    async def scenario(user) -> None:  # noqa: ANN001
        await user.open("/results")
        user.find(kind=ui.button, content="Profile").click()
        await user.should_see("Profile: Alex Martin")
        await user.should_see("executive_summary")
        await user.should_see("Builds dashboards in Power BI for public agencies.")

    run_ui(service, scenario)


def test_verdict_chip_colour_follows_the_score_bands(service: AppService) -> None:
    assert score_shade(service, 95, fit=True) == SCORE_SHADE["strong"] == "#5BBE77"
    assert score_shade(service, 90, fit=True) == SCORE_SHADE["strong"]
    assert score_shade(service, 89, fit=True) == SHADE["green"]
    assert score_shade(service, 70, fit=False) == SHADE["green"]  # by score, not verdict
    assert score_shade(service, 69, fit=True) == SHADE["yellow"]
    assert score_shade(service, 40, fit=False) == SHADE["yellow"]
    assert score_shade(service, 39, fit=False) == SCORE_SHADE["low"]
    # No score (judged but not described): the verdict's colour.
    assert score_shade(service, None, fit=True) == SHADE["green"]
    assert score_shade(service, None, fit=False) == SHADE["yellow"]


def test_title_case_for_job_titles() -> None:
    assert title_case("senior data analyst (contract)") == "Senior Data Analyst (Contract)"
    assert title_case("head of UX and SQL reporting") == "Head of UX and SQL Reporting"
    assert title_case("analyste de données - mandat") == "Analyste de Données - Mandat"
    assert title_case("l'équipe iOS") == "L'équipe iOS"
    assert title_case("the iOS developer") == "The iOS Developer"
