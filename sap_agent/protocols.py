"""Structural protocols for browser and network doubles (issue #684 follow-up).

``PageLike`` is the single typing seam between tools and Playwright: every
tool accepts ``PageLike`` instead of the concrete ``playwright.sync_api.Page``,
so unit tests can pass lightweight fakes without a browser.

Shapes mirror the real ``Page`` exactly: same parameter names, keyword-only
markers, and ``Any`` where Playwright uses narrow literals. ``ty`` checks
keyword compatibility, so even a renamed parameter breaks conformance.
The concrete ``Page`` satisfies this protocol structurally.

``CaptureLike`` does the same for ``NetworkCapture`` (see tools/network.py).
"""

from __future__ import annotations

from typing import Any, Protocol


class PageLike(Protocol):
    """Minimal browser-page surface used by sap_agent tools."""

    @property
    def url(self) -> str: ...
    @property
    def viewport_size(self) -> Any: ...

    def locator(self, selector: str) -> Any: ...
    def get_by_text(self, text: str, *, exact: bool = False) -> Any: ...
    def evaluate(self, expression: str, arg: Any = None) -> Any: ...
    def goto(self, url: str, *, wait_until: Any = None, timeout: Any = None) -> Any: ...
    def title(self) -> str: ...
    def screenshot(self, *, path: Any = None, full_page: bool | None = False) -> Any: ...
    def wait_for_selector(self, selector: str, *, state: Any = "attached", timeout: Any = None) -> Any: ...
    def wait_for_function(self, expression: str, *, arg: Any = None, timeout: Any = None) -> Any: ...
    def wait_for_url(self, url: Any, *, timeout: Any = None) -> Any: ...
    def wait_for_load_state(self, state: Any = None, *, timeout: Any = None) -> Any: ...


# Page.on is excluded: Playwright stubs type it as ~20 @overload variants
# that ty cannot match against a single protocol member, so event
# subscription (NetworkCapture) takes Any instead.


class CaptureLike(Protocol):
    """Minimal network-capture surface used by answer/discover/qa tools."""

    def capture_response_urls(self, url_substring: str | None = None) -> list[str]: ...
    def response_body(self, url: str) -> Any: ...
    def latest_response_body(self, url_substring: str) -> Any: ...


class ResponseLike(Protocol):
    """Minimal response surface for NetworkCapture (real or fake)."""

    @property
    def url(self) -> str: ...

    headers: Any

    def json(self) -> Any: ...
