"""Network/OData capture over a Playwright page session (issue #650).

Captures same-origin response URLs and lets callers retrieve the latest JSON
body for a URL substring. Trace/context records URLs only — payload bodies and
credentials never enter logs (security rule from #645).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from playwright.sync_api import Error as PlaywrightError

if TYPE_CHECKING:
    from ..protocols import ResponseLike

#: JSON-shaped values flowing through capture (bodies, payloads)
JsonValue = dict[str, "JsonValue"] | list["JsonValue"] | str | int | float | bool | None

#: static-asset suffixes never carrying queryable JSON — skip eager parse (perf)
_NON_DATA_SUFFIXES = (
    ".js",
    ".css",
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".map",
)

#: UI5 framework paths never carrying app data
_NON_DATA_SUBSTRINGS = ("/resources/", "/test-resources/", "sap-ui")


class NetworkCapture:
    """Records same-origin responses; bodies fetched lazily on demand.

    Stored bodies are bounded (`max_bodies`, LRU-evicted) so long sessions or
    chatty apps cannot grow memory without limit (#658). The URL history list
    stays complete — URLs are tiny strings.
    """

    def __init__(self, page: Any, app_origin: str, max_bodies: int = 100, max_urls: int = 1_000) -> None:
        self.app_origin = app_origin.rstrip("/")
        self.max_bodies = max_bodies
        self.max_urls = max_urls
        self._urls: list[str] = []
        self._bodies: dict[str, JsonValue] = {}
        page.on("response", self._on_response)

    def _should_parse(self, url: str, headers: dict) -> bool:
        lowered = url.lower()
        if lowered.endswith(_NON_DATA_SUFFIXES):
            return False
        if any(s in lowered for s in _NON_DATA_SUBSTRINGS):
            return False
        content_type = ""
        try:
            content_type = str(headers.get("content-type", "") or headers.get("Content-Type", "")).lower()
        except (AttributeError, TypeError, ValueError):
            content_type = ""
        if content_type and "json" not in content_type:
            return False
        try:
            length = headers.get("content-length", headers.get("Content-Length", ""))
            if length and int(str(length).strip()) > 5_000_000:
                return False
        except (AttributeError, TypeError, ValueError):
            pass
        return True

    def _on_response(self, response: ResponseLike) -> None:
        if response.url.startswith(self.app_origin):
            self._urls.append(response.url)
            if len(self._urls) > self.max_urls:
                del self._urls[: len(self._urls) - self.max_urls]
            headers: dict = {}
            try:
                headers = response.headers or {}
            except (PlaywrightError, AttributeError, TypeError):
                headers = {}
            # Unknown shapes (test fakes without headers) still parse unless the
            # URL itself is a static asset — only skip on positive non-JSON signal.
            if not self._should_parse(response.url, headers):
                return
            try:
                body = response.json()
            except (PlaywrightError, ValueError, TypeError):
                body = None
            if body is not None:
                self._store_body(response.url, body)

    def _store_body(self, url: str, body: JsonValue) -> None:
        """Insert as most-recent entry; evict least-recent beyond max_bodies."""
        self._bodies.pop(url, None)
        self._bodies[url] = body
        while len(self._bodies) > self.max_bodies:
            self._bodies.pop(next(iter(self._bodies)))

    def _touch(self, url: str) -> None:
        if url in self._bodies:
            self._bodies[url] = self._bodies.pop(url)

    def capture_response_urls(self, url_substring: str | None = None) -> list[str]:
        if url_substring is None:
            return list(self._urls)
        return [u for u in self._urls if url_substring in u]

    def latest_response_body(self, url_substring: str) -> JsonValue:
        """Latest JSON body whose URL contains the substring, or None."""
        for url in reversed(self._urls):
            if url_substring in url and url in self._bodies:
                self._touch(url)
                return self._bodies[url]
        return None

    def response_body(self, url: str) -> JsonValue:
        """JSON body for this exact URL, or None."""
        body = self._bodies.get(url)
        if body is not None:
            self._touch(url)
        return body

    def response_payloads(self) -> dict[str, JsonValue]:
        return dict(self._bodies)

    def matches_fixture(self, url_substring: str, expected: list[dict[str, Any]]) -> bool:
        body = self.latest_response_body(url_substring)
        return json.loads(json.dumps(body)) == expected
