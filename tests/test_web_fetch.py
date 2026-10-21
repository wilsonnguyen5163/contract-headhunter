"""The fetcher, against a fake network (httpx.MockTransport): nothing leaves the machine."""

from collections.abc import Callable

import httpx
import pytest

from jobmatch.config import WebSourceSettings
from jobmatch.sources.web import FetchError, WebFetcher

SETTINGS = WebSourceSettings(
    user_agent="jobmatch-test",
    delay_seconds=2,
    timeout_seconds=5,
    max_bytes=10_000,
    max_redirects=3,
    respect_robots_txt=True,
    blocked_host_labels=["indeed"],
)
HTML = {"content-type": "text/html; charset=utf-8"}


def fetcher_for(
    handler: Callable[[httpx.Request], httpx.Response], *, sleeps: list[float] | None = None
) -> WebFetcher:
    clock = iter(float(i) for i in range(10_000))
    return WebFetcher(
        SETTINGS,
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
        clock=lambda: next(clock),
    )


def site(pages: dict[str, httpx.Response]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return pages.get(str(request.url), httpx.Response(404))

    return handler


def test_fetches_html_page() -> None:
    fetcher = fetcher_for(
        site({"https://jobs.example.ca/1": httpx.Response(200, headers=HTML, text="<p>Job</p>")})
    )
    page = fetcher.fetch("https://jobs.example.ca/1")
    assert (page.status, page.html, page.final_url) == (
        200,
        "<p>Job</p>",
        "https://jobs.example.ca/1",
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://ca.indeed.com/viewjob?jk=1",
        "https://www.indeed.ca/job/2",
        "ftp://jobs.example.ca/1",
    ],
)
def test_blocked_links_are_refused_without_a_request(url: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be sent")

    fetcher = fetcher_for(handler)
    assert fetcher.blocked_reason(url) is not None
    with pytest.raises(FetchError):
        fetcher.fetch(url)


def test_linkedin_links_are_allowed() -> None:
    assert (
        fetcher_for(site({})).blocked_reason("https://www.linkedin.com/jobs/view/4012345678/")
        is None
    )


def test_redirect_is_followed_but_not_to_a_blocked_host() -> None:
    fetcher = fetcher_for(
        site(
            {
                "https://short.example/a": httpx.Response(302, headers={"location": "/b"}),
                "https://short.example/b": httpx.Response(200, headers=HTML, text="ok"),
                "https://short.example/c": httpx.Response(
                    301, headers={"location": "https://ca.indeed.com/viewjob?jk=9"}
                ),
            }
        )
    )
    assert fetcher.fetch("https://short.example/a").final_url == "https://short.example/b"
    with pytest.raises(FetchError, match="never fetched"):
        fetcher.fetch("https://short.example/c")


def test_robots_txt_is_respected() -> None:
    robots = httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
    fetcher = fetcher_for(
        site(
            {
                "https://jobs.example.ca/robots.txt": robots,
                "https://jobs.example.ca/open/1": httpx.Response(200, headers=HTML, text="ok"),
            }
        )
    )
    assert fetcher.fetch("https://jobs.example.ca/open/1").html == "ok"
    with pytest.raises(FetchError, match="robots.txt"):
        fetcher.fetch("https://jobs.example.ca/private/2")


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(404), "HTTP 404"),
        (httpx.Response(429), "HTTP 429"),  # e.g. a sign-in wall or rate limit
        (
            httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF"),
            "not an HTML",
        ),
        (httpx.Response(200, headers=HTML, text="x" * 20_000), "larger than"),
    ],
)
def test_unusable_responses_are_errors(response: httpx.Response, message: str) -> None:
    fetcher = fetcher_for(site({"https://jobs.example.ca/1": response}))
    with pytest.raises(FetchError, match=message):
        fetcher.fetch("https://jobs.example.ca/1")


def test_network_failure_is_reported_not_raised_raw() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(FetchError, match="network error"):
        fetcher_for(handler).fetch("https://jobs.example.ca/1")


def test_requests_are_spaced_out() -> None:
    sleeps: list[float] = []
    fetcher = fetcher_for(
        site(
            {
                f"https://jobs.example.ca/{i}": httpx.Response(200, headers=HTML, text="ok")
                for i in range(3)
            }
        ),
        sleeps=sleeps,
    )
    for i in range(3):
        fetcher.fetch(f"https://jobs.example.ca/{i}")
    # robots.txt + 3 pages = 4 requests; the fake clock advances 1 s per reading,
    # so each later request waits the remainder of the 2 s delay.
    assert sleeps and all(0 < s <= SETTINGS.delay_seconds for s in sleeps)
