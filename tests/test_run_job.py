"""A run keeps going when the reviewer changes tab (user request 2026-11-11): it lives in a
RunJob, not in the Find matches screen; the header shows it on every tab. Fake Gemini that
waits until the test lets it answer. No network."""

import threading
from pathlib import Path

from jobmatch.app.service import AppService
from jobmatch.app.ui import run_job
from jobmatch.config import Settings
from jobmatch.llm.provider import LlmRequest, LlmResponse
from test_app_ui import run_ui
from test_resumes import folder
from test_talent import MarkerGemini


class SlowGemini(MarkerGemini):
    """Answers only once `go` is set, so the run is still going while the test changes tab."""

    def __init__(self) -> None:
        super().__init__()
        self.go = threading.Event()

    def generate(self, request: LlmRequest) -> LlmResponse:
        assert self.go.wait(timeout=20), "the test never let the fake answer"
        return super().generate(request)


def test_run_survives_a_tab_change(app_settings: Settings, store, make_job, tmp_path: Path):  # noqa: ANN001
    from jobmatch.storage.csv_store import CsvJobRepository

    fake = SlowGemini()
    service = AppService(
        app_settings, tmp_path / "app.env", provider_factory=lambda: fake, environ={}
    )
    CsvJobRepository(store).add_new([
        make_job(job_id="paste:1", source_id="1", title_norm="Data Analyst (contract)",
                 responsibilities=["Build Power BI dashboards"]),
    ])  # fmt: skip
    service.pick_contractor_folder(folder(tmp_path))

    async def scenario(user) -> None:  # noqa: ANN001
        await user.open("/")
        user.find(marker="find-matches").click()
        await user.should_see(marker="run-indicator", retries=20)
        await user.open("/jobs")  # another tab while Gemini is working
        await user.should_see("Matching…", retries=20)
        assert run_job(service).running
        fake.go.set()
        await user.open("/")
        await user.should_see("Done:", retries=80)

    run_ui(service, scenario)
    job = run_job(service)
    assert not job.running and job.error is None and fake.calls >= 1
    assert job.report is not None and job.report.jobs_matched == 1
