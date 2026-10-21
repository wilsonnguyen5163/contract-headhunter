"""End-to-end link import with a fake fetcher: links -> page -> RawJob -> normalize -> jobs.csv."""

from datetime import UTC, datetime
from pathlib import Path

from jobmatch.ingest import ingest_links
from jobmatch.normalize.pipeline import Normalizer
from jobmatch.schemas import Commitment, EmploymentType, PayUnit, RemoteType
from jobmatch.sources.web import FetchedPage, FetchError
from jobmatch.storage.csv_store import CsvJobRepository, CsvStore
from jobmatch.storage.raw_store import RawStore

PAGES = Path(__file__).resolve().parent / "fixtures" / "pages"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
FTC = "https://jobs.example.ca/job/senior-campaign-producer-6-month-ftc/?utm_source=alert"
FR = "https://emplois.exemple.ca/postes/analyste-12-mois"


class FakeFetcher:
    """Serves fixture pages; records every URL it is asked for."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def blocked_reason(self, url: str) -> str | None:
        return "never fetched automatically; paste by hand" if "indeed." in url else None

    def fetch(self, url: str) -> FetchedPage:
        self.requested.append(url)
        if url not in self.pages:
            raise FetchError("HTTP 404")
        return FetchedPage(requested_url=url, final_url=url, status=200, html=self.pages[url])


def pages() -> dict[str, str]:
    return {
        "https://jobs.example.ca/job/senior-campaign-producer-6-month-ftc": (
            PAGES / "jobposting_ftc.html"
        ).read_text(encoding="utf-8"),
        FR: (PAGES / "no_jsonld.html").read_text(encoding="utf-8"),
    }


def links_file(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "links.txt"
    path.write_text(
        "# test links\n"
        f"{FTC}   # from an alert\n"
        f"{FR}\n"
        "https://ca.indeed.com/viewjob?jk=123\n"
        "https://jobs.example.ca/job/gone\n"
        "not-a-link\n"
        f"{FTC}\n",
        encoding="utf-8",
    )
    return path


def run(normalizer: Normalizer, data_dir: Path, fetcher: FakeFetcher, links: Path, **kw):
    return ingest_links(
        [links], data_dir=data_dir, lock_timeout=5, normalizer=normalizer, fetcher=fetcher,
        now=NOW, **kw,
    )  # fmt: skip


def test_links_are_fetched_normalized_and_stored(normalizer: Normalizer, tmp_path: Path) -> None:
    fetcher = FakeFetcher(pages())
    report = run(normalizer, tmp_path / "data", fetcher, links_file(tmp_path / "inbox"))

    by_line = {o.file: o for o in report.outcomes}
    assert by_line["links.txt:2"].outcome == "new"
    assert by_line["links.txt:3"].outcome == "new"
    assert by_line["links.txt:4"].outcome == "skipped"  # Indeed: paste by hand
    assert by_line["links.txt:5"].outcome == "error"  # fetch failed
    assert by_line["links.txt:6"].outcome == "error"  # not a URL
    assert by_line["links.txt:7"].detail == "listed twice"
    assert report.fetched == 3  # the two pages and the failed one; Indeed never requested

    jobs = {
        j.title_raw: j
        for j in CsvJobRepository(CsvStore(tmp_path / "data", lock_timeout=5)).list_all()
    }
    ftc = jobs["Senior Campaign Producer - 6 month FTC"]
    assert ftc.company_norm == "example agency"
    assert (ftc.employment_type, ftc.commitment) == (EmploymentType.CONTRACT, Commitment.FULL_TIME)
    assert ftc.duration_months == 6.0
    assert ftc.remote_type is RemoteType.REMOTE
    assert (ftc.province, ftc.country) == ("CA-BC", "CA")
    assert (ftc.pay_min, ftc.pay_max, ftc.pay_unit, ftc.pay_currency) == (
        70000,
        80000,
        PayUnit.YEAR,
        "CAD",
    )
    assert ftc.requirements[0] == "5+ years of experience in digital project management"
    assert ftc.url == "https://jobs.example.ca/job/senior-campaign-producer-6-month-ftc"  # no utm_
    fr = jobs["Analyste d'affaires – contrat de 12 mois | Exemple Emplois"]
    assert fr.employment_type is EmploymentType.CONTRACT
    assert fr.years_experience_min == 6.0


def test_second_run_reads_the_cache_not_the_network(normalizer: Normalizer, tmp_path: Path) -> None:
    links = links_file(tmp_path / "inbox")
    run(normalizer, tmp_path / "data", FakeFetcher(pages()), links)

    again = FakeFetcher(pages())
    report = run(normalizer, tmp_path / "data", again, links)
    assert again.requested == ["https://jobs.example.ca/job/gone"]  # only the one never stored
    assert [o.outcome for o in report.outcomes if o.file in ("links.txt:2", "links.txt:3")] == [
        "already_stored",
        "already_stored",
    ]

    refreshed = FakeFetcher(pages())
    run(normalizer, tmp_path / "data", refreshed, links, refresh=True)
    assert len(refreshed.requested) == 3


def test_reset_keeps_cache_so_rebuild_needs_no_network(
    normalizer: Normalizer, tmp_path: Path
) -> None:
    data_dir, links = tmp_path / "data", links_file(tmp_path / "inbox")
    run(normalizer, data_dir, FakeFetcher(pages()), links)
    (data_dir / "jobs.csv").unlink()
    (data_dir / "jobs.schema.json").unlink()

    rebuild = FakeFetcher(pages())
    report = run(normalizer, data_dir, rebuild, links)
    assert rebuild.requested == ["https://jobs.example.ca/job/gone"]
    assert report.count("new") == 2


def test_dry_run_writes_nothing(normalizer: Normalizer, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    report = run(
        normalizer, data_dir, FakeFetcher(pages()), links_file(tmp_path / "inbox"), dry_run=True
    )
    assert report.count("new") == 2
    assert not (data_dir / "jobs.csv").exists()
    assert RawStore(data_dir / "raw", lock_timeout=5).read("manual") == []
