"""Fetch one posting page per link that a person pasted (data/inbox/links/).

Plain HTTP GET only: no cookies, no logins, no JavaScript. One request at a time with a
pause between requests; robots.txt is respected; hosts on the block list (Indeed) and
redirects to them are refused; non-HTML and oversized responses are refused.
Settings: config/sources.yaml. The page body is untrusted data and is only parsed.
"""

import codecs
import re
import time
from collections.abc import Callable
from typing import Protocol
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import BaseModel

from jobmatch.config import WebSourceSettings


class FetchError(Exception):
    """A page that could not be fetched; the message says why (shown in the report).
    `status` is the HTTP status when the server answered (404 = the posting is gone)."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# <meta charset="..."> or <meta http-equiv="Content-Type" content="...; charset=...">
_META_CHARSET = re.compile(rb"<meta[^>]+charset\s*=\s*[\"']?\s*([A-Za-z0-9._:-]+)", re.IGNORECASE)


def decode_page(body: bytes, header_charset: str | None) -> str:
    """The HTTP header's charset, else the page's own <meta charset>, else UTF-8 with a
    Windows-1252 fallback (older French sites often send Latin-1 without saying so)."""
    for candidate in (header_charset, _meta_charset(body)):
        if candidate:
            try:
                codecs.lookup(candidate)
            except LookupError:
                continue
            return body.decode(candidate, errors="replace")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


def _meta_charset(body: bytes) -> str | None:
    match = _META_CHARSET.search(body[:4096])
    return match.group(1).decode("ascii", errors="ignore") if match else None


class FetchedPage(BaseModel):
    requested_url: str
    final_url: str
    status: int
    html: str


class PageFetcher(Protocol):
    def blocked_reason(self, url: str) -> str | None: ...

    def fetch(self, url: str) -> FetchedPage: ...


class WebFetcher:
    def __init__(
        self,
        settings: WebSourceSettings,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._client = client or httpx.Client(
            timeout=settings.timeout_seconds,
            follow_redirects=False,
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
                "Accept-Language": "en-CA,en;q=0.9,fr-CA;q=0.8,fr;q=0.7",
            },
        )
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self._robots: dict[str, RobotFileParser | None] = {}
        self._blocked = {label.casefold() for label in settings.blocked_host_labels}

    def close(self) -> None:
        self._client.close()

    def blocked_reason(self, url: str) -> str | None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return "not an http(s) link"
        labels = set(parts.hostname.casefold().split("."))
        if labels & self._blocked:
            return f"{parts.hostname} is never fetched automatically; paste the posting by hand"
        return None

    def fetch(self, url: str) -> FetchedPage:
        current = url
        for _ in range(self._settings.max_redirects + 1):
            reason = self.blocked_reason(current)
            if reason:
                raise FetchError(reason)
            if not self._robots_allow(current):
                host = urlsplit(current).hostname
                raise FetchError(f"robots.txt of {host} disallows this page")
            status, location, body, content_type, encoding = self._get(current)
            if status in (301, 302, 303, 307, 308) and location:
                current = urljoin(current, location)
                continue
            if status >= 400:
                raise FetchError(f"HTTP {status}", status=status)
            if "html" not in content_type:
                raise FetchError(f"not an HTML page ({content_type or 'no content type'})")
            html = decode_page(body, encoding)
            return FetchedPage(requested_url=url, final_url=current, status=status, html=html)
        raise FetchError(f"more than {self._settings.max_redirects} redirects")

    def _wait_turn(self) -> None:
        if self._last_request is not None:
            remaining = self._settings.delay_seconds - (self._clock() - self._last_request)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request = self._clock()

    def _get(self, url: str) -> tuple[int, str | None, bytes, str, str | None]:
        self._wait_turn()
        try:
            with self._client.stream("GET", url) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    return response.status_code, response.headers.get("location"), b"", "", None
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > self._settings.max_bytes:
                        raise FetchError(f"page larger than {self._settings.max_bytes} bytes")
                content_type = response.headers.get("content-type", "").casefold()
                return (
                    response.status_code,
                    None,
                    bytes(body),
                    content_type,
                    response.charset_encoding,
                )
        except httpx.TimeoutException as exc:
            raise FetchError(f"timed out after {self._settings.timeout_seconds} s") from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"network error: {type(exc).__name__}") from exc

    def _robots_allow(self, url: str) -> bool:
        if not self._settings.respect_robots_txt:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        parser = self._robots[origin]
        return parser is None or parser.can_fetch(self._settings.user_agent, url)

    def _load_robots(self, origin: str) -> RobotFileParser | None:
        """None means "no rules" (missing or unreadable robots.txt allows everything)."""
        try:
            status, _, body, _, encoding = self._get(f"{origin}/robots.txt")
        except FetchError:
            return None
        if status != 200:
            return None
        parser = RobotFileParser()
        parser.parse(body.decode(encoding or "utf-8", errors="replace").splitlines())
        return parser
