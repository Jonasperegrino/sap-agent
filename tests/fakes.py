"""Shared mock Playwright objects (#660).

Canonical primitives for tool unit tests: the fixture-matrix table family
(``FakeTablePage``/``FakeLocator``/``FakeRowList``/``FakeRow``/``FakeCellList``),
network fakes (``FakeResponse``, ``FakeCapture``), and QA-tool recorders.
File-specific page routing (e.g. discover's selector map) stays local to its
test file — shared primitives here, no god-fake.
"""

from __future__ import annotations

from typing import Any

CELL_SELECTOR = "td, [role='cell']"
TABLE_HEADERS = ("Order ID", "Customer", "Amount", "Status", "Built")

#: default canned payload so ``FakeCapture(urls)`` behaves like the original
#: discover-local fake (sales.json probe returns two order stubs)
DEFAULT_SALES_BODIES: dict[str, object] = {
    "sales.json": [{"id": "SO-1001"}, {"id": "SO-1002"}],
}


class PageStub:
    """No-op PageLike base: inherit and override only what the test needs.

    Signatures mirror ``PageLike`` exactly (no ``**kwargs``) so overrides
    stay LSP-compatible. Unused members raise loudly so missing overrides
    fail fast instead of returning None into tool logic.
    """

    url: str = ""
    viewport_size: Any = None

    def locator(self, selector: str) -> Any:
        raise AssertionError(f"unexpected locator({selector!r})")

    def get_by_text(self, text: str, *, exact: bool = False) -> Any:
        raise AssertionError(f"unexpected get_by_text({text!r})")

    def evaluate(self, expression: str, arg: Any = None) -> Any:
        # probe-friendly default: tools use evaluate as an optional fast path
        # (see _extract_via_evaluate) and fall back when it yields nothing.
        return None

    def goto(self, url: str, *, wait_until: Any = None, timeout: Any = None) -> Any:
        raise AssertionError(f"unexpected goto({url!r})")

    def title(self) -> str:
        raise AssertionError("unexpected title()")

    def screenshot(self, *, path: Any = None, full_page: bool | None = False) -> Any:
        raise AssertionError("unexpected screenshot()")

    def wait_for_selector(self, selector: str, *, state: Any = "attached", timeout: Any = None) -> Any:
        # no-op default: waiting always succeeds instantly unless overridden.
        return None

    def wait_for_function(self, expression: str, *, arg: Any = None, timeout: Any = None) -> Any:
        return None

    def wait_for_url(self, url: Any, *, timeout: Any = None) -> Any:
        return None

    def wait_for_load_state(self, state: Any = None, *, timeout: Any = None) -> Any:
        return None


class FakeResponse:
    """HTTP response stub: URL + JSON body + optional headers."""

    def __init__(self, url: str, body: object, headers: dict | None = None) -> None:
        self._url = url
        self._body = body
        self.headers: dict = headers or {}

    @property
    def url(self) -> str:
        return self._url

    def json(self) -> object:
        if not isinstance(self._body, (dict, list)):
            raise ValueError("not json")
        return self._body


class FakeCapture:
    """NetworkCapture stand-in: canned URLs + substring-keyed bodies."""

    def __init__(self, urls: list[str], bodies: dict[str, object] | None = None) -> None:
        self._urls = urls
        self._bodies = DEFAULT_SALES_BODIES if bodies is None else bodies

    def capture_response_urls(self, url_substring: str | None = None) -> list[str]:
        if url_substring:
            return [u for u in self._urls if url_substring in u]
        return self._urls

    def response_body(self, url: str):
        for substring, body in self._bodies.items():
            if substring in url:
                return body
        return None

    def latest_response_body(self, url_substring: str):
        for url in reversed(self._urls):
            if url_substring in url:
                return self.response_body(url)
        return None


class FakeCellList:
    def __init__(self, texts: list[str]) -> None:
        self._texts = texts

    def all_inner_texts(self):
        return [t + "\n" for t in self._texts]


class FakeRow:
    def __init__(self, texts: list[str]) -> None:
        self._texts = texts

    def locator(self, selector: str):
        assert selector == CELL_SELECTOR
        return FakeCellList(self._texts)


class FakeRowList:
    def __init__(self, rows: list[list[str]]) -> None:
        self._rows = [FakeRow(r) for r in rows]

    def all(self):
        return self._rows


class FakeLocator:
    """Selector-driven locator: header texts on th probes, fixture rows via all()."""

    def __init__(self, selector: str, rows: list[list[str]]) -> None:
        self._selector = selector
        self._rows = rows

    @property
    def first(self):
        return self

    def wait_for(self, state: str = "visible", timeout: int = 15_000) -> None:
        pass

    def locator(self, selector: str):
        return FakeLocator(selector, self._rows)

    def all_inner_texts(self):
        if "th" in self._selector or "columnheader" in self._selector:
            return [h + "\n" for h in TABLE_HEADERS]
        return []

    def click(self, timeout: int | None = None) -> None:
        pass

    def fill(self, value: str) -> None:
        pass

    def inner_text(self) -> str:
        return ""

    def count(self) -> int:
        return len(self._rows)

    def all(self):
        return FakeRowList(self._rows).all()


class FakeTablePage(PageStub):
    """Page whose any locator serves headers + the given fixture rows."""

    url = "http://localhost:8080/#/dashboard"

    def __init__(self, rows: list[list[str]]) -> None:
        self._rows = rows

    def locator(self, selector: str):
        return FakeLocator(selector, self._rows)


class ScreenshotRecordingPage(PageStub):
    """Records full-page screenshots; locator() yields recording elements."""

    def __init__(self) -> None:
        self.screenshot_calls: list[dict] = []
        self.viewport_size = {"width": 1280, "height": 800}

    def wait_for_load_state(self, state=None, timeout=None, **kwargs) -> None:
        pass

    def screenshot(self, path=None, full_page: bool | None = False, **kwargs):
        self.screenshot_calls.append({"path": str(path), "full_page": full_page})
        return b"png-bytes"

    def locator(self, selector: str):
        return ScreenshotRecordingElement()


class ScreenshotRecordingElement:
    def __init__(self) -> None:
        self.screenshot_calls: list[dict] = []
        self.box = {"width": 120.0, "height": 40.0}

    @property
    def first(self):
        return self

    def wait_for(self, state: str = "visible", timeout: int | None = None) -> None:
        pass

    def bounding_box(self):
        return dict(self.box)

    def screenshot(self, path=None):
        self.screenshot_calls.append({"path": str(path)})
        return b"png-bytes"


class ScriptedEvaluatePage(PageStub):
    """Returns queued evaluate() results in order; records every expression.

    Feeds accessibility/UX/performance audits without a browser: queue the
    JS result each audit expects, then assert on the findings.
    """

    def __init__(self, results: list[object]) -> None:
        self._results = list(results)
        self.expressions: list[str] = []

    def evaluate(self, expression: str, arg: Any = None):
        self.expressions.append(expression)
        return self._results.pop(0) if self._results else None
