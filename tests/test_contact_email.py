"""HR contact emails from posting text (TODO 1, user decisions 2026-11-10): English and
French postings, automated mailboxes skipped, masked before Gemini, filled for jobs stored
earlier. No network."""

from collections.abc import Callable

import pytest

from jobmatch.config import Settings
from jobmatch.llm.payload import job_card, render_job
from jobmatch.normalize.contact import contact_email
from jobmatch.pipeline import fill_contact_emails
from jobmatch.schemas import Job
from jobmatch.storage.loader import create_loader


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Please send your resume to Jane.Doe@Example-Insurance.ca by Friday.",
         "jane.doe@example-insurance.ca"),
        ("Faites parvenir votre CV à recrutement@ville.quebec.qc.ca avant le 30 novembre.",
         "recrutement@ville.quebec.qc.ca"),
        ("Questions? noreply@ats.com cannot answer; write to talent@acme.ca.", "talent@acme.ca"),
        ("Courriel : ne-pas-repondre@emploi.ca. Postulez via le portail.", None),
        ("<img src='logo@2x.png'> Apply online.", None),
        ("No email here, apply on our site.", None),
        ("", None),
    ],
)  # fmt: skip
def test_contact_email(text: str, expected: str | None) -> None:
    assert contact_email(text) == expected


def test_email_masked_for_gemini(app_settings: Settings, make_job: Callable[..., Job]) -> None:
    assert app_settings.llm is not None
    job = make_job(
        responsibilities=["Envoyer les rapports à marie.tremblay@ville.qc.ca chaque semaine"],
        contact_email="marie.tremblay@ville.qc.ca",
    )
    text = render_job(job_card(job, app_settings.llm.payload))
    assert "marie.tremblay" not in text and "[email]" in text


def test_jobs_stored_earlier_get_their_email(
    app_settings: Settings, make_job: Callable[..., Job]
) -> None:
    loader = create_loader(app_settings)
    loader.jobs.add_new([
        make_job(job_id="paste:1", source_id="1",
                 description_raw="Send your CV to hr@example.ca. Contract, 6 months."),
        make_job(job_id="paste:2", source_id="2", description_raw="Apply on our website."),
    ])  # fmt: skip
    assert fill_contact_emails(loader) == 1
    assert loader.jobs.get("paste:1").contact_email == "hr@example.ca"  # type: ignore[union-attr]
    assert fill_contact_emails(loader) == 0  # settled: nothing rewritten again


def test_cards_show_email_and_link_both_ways(app_settings, make_job, tmp_path) -> None:  # noqa: ANN001
    """TODO 1 and 2: the email row with its copy button, and See on Results / See on Jobs."""
    import asyncio

    from nicegui.testing.user_simulation import user_simulation

    from jobmatch.app.service import AppService
    from jobmatch.app.ui import build_root
    from test_talent import MarkerGemini

    loader = create_loader(app_settings)
    loader.jobs.add_new([
        make_job(job_id="paste:1", source_id="1", title_norm="Analyste de données (contrat)",
                 contact_email="recrutement@ville.qc.ca"),
        make_job(job_id="paste:2", source_id="2", title_norm="Data Analyst (contract)"),
    ])  # fmt: skip
    service = AppService(
        app_settings, tmp_path / "app.env", provider_factory=MarkerGemini, environ={}
    )

    async def scenario(user) -> None:  # noqa: ANN001
        await user.open("/jobs")
        await user.should_see("recrutement@ville.qc.ca")
        await user.should_see("No contact email in the posting")
        await user.should_see(marker="copy-email:paste:1")
        user.find(marker="see-results:paste:1").click()
        await user.should_see("Results")
        await user.should_see(marker="see-jobs:paste:2")
        user.find(marker="see-jobs:paste:2").click()
        await user.should_see(marker="see-results:paste:2")

    async def main() -> None:
        async with user_simulation(root=build_root(service, native=False)) as user:
            await scenario(user)

    asyncio.run(main())
