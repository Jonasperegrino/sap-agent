"""Network/OData capture over a Playwright page session (issue #650).

Captures same-origin response URLs and lets callers retrieve the latest JSON
body for a URL substring. Trace/context records URLs only — payload bodies and
credentials never enter logs (security rule from #645).
"""

from __future__ import annotations

import json
from typing import Any

from playwright.sync_api import Page, Response


class NetworkCapture:
    """Records same-origin responses; bodies fetched lazily on demand.

    Stored bodies are bounded (`max_bodies`, LRU-evicted) so long sessions or
    chatty apps cannot grow memory without limit (#658). The URL history list
    stays complete — URLs are tiny strings.
    """

    def __init__(self, page: Page, app_origin: str, max_bodies: int = 100) -> None:
        self.app_origin = app_origin.rstrip("/")
        self.max_bodies = max_bodies
        self._urls: list[str] = []
        self._bodies: dict[str, Any] = {}
        page.on("response", self._on_response)

    def _on_response(self, response: Response) -> None:
        if response.url.startswith(self.app_origin):
            self._urls.append(response.url)
            try:
                body = response.json()
            except Exception:
                body = None
            if body is not None:
                self._store_body(response.url, body)

    def _store_body(self, url: str, body: Any) -> None:
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

    def latest_response_body(self, url_substring: str) -> Any:
        """Latest JSON body whose URL contains the substring, or None."""
        for url in reversed(self._urls):
            if url_substring in url and url in self._bodies:
                self._touch(url)
                return self._bodies[url]
        return None

    def response_body(self, url: str) -> Any:
        """JSON body for this exact URL, or None."""
        body = self._bodies.get(url)
        if body is not None:
            self._touch(url)
        return body

    def response_payloads(self) -> dict[str, Any]:
        return dict(self._bodies)

    def matches_fixture(self, url_substring: str, expected: list[dict[str, Any]]) -> bool:
        body = self.latest_response_body(url_substring)
        return json.loads(json.dumps(body)) == expected
