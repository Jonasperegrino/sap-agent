"""Unit tests for UI5 bridge (issue #645): semantic reads of the running app."""

from __future__ import annotations

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from sap_agent.ui5.bridge import current_route, has_login_form, wait_for_ui5_ready, welcome_text


class FakePage:
    def __init__(self, url: str = "http://localhost:8080/#/dashboard") -> None:
        self.url = url
        self._fail_selectors: set[str] = set()
        self._wait_for_calls: list[dict] = []

    def wait_for_selector(self, selector: str, state: str = "attached", timeout: int = 30_000) -> None:
        self._wait_for_calls.append({"selector": selector, "state": state, "timeout": timeout})
        if selector in self._fail_selectors:
            raise PlaywrightTimeoutError(f"Selector {selector} not found")

    def locator(self, selector: str):
        return FakeLocator(fail=self._fail_selectors is not None and selector in self._fail_selectors)


class FakeLocator:
    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    @property
    def first(self):
        return self

    def wait_for(self, state: str = "visible", timeout: int = 10_000) -> None:
        if self._fail:
            raise PlaywrightTimeoutError("not found")

    def inner_text(self) -> str:
        return "  Welcome  "


class TestCurrentRoute:
    def test_extracts_hash_route(self) -> None:
        page = FakePage(url="http://localhost:8080/#/dashboard")
        assert current_route(page) == "/dashboard"

    def test_returns_none_when_no_hash(self) -> None:
        page = FakePage(url="http://localhost:8080/")
        assert current_route(page) is None

    def test_deep_route(self) -> None:
        page = FakePage(url="http://localhost:8080/#/orders/detail/123")
        assert current_route(page) == "/orders/detail/123"


class TestWaitForUi5Ready:
    def test_waits_for_sap_ui_body(self) -> None:
        page = FakePage()
        wait_for_ui5_ready(page, timeout_ms=5000)
        assert page._wait_for_calls[0]["selector"] == ".sapUiBody"

    def test_passes_timeout(self) -> None:
        page = FakePage()
        wait_for_ui5_ready(page, timeout_ms=10_000)
        assert page._wait_for_calls[0]["timeout"] == 10_000


class TestHasLoginForm:
    def test_returns_true_when_found(self) -> None:
        page = FakePage()
        assert has_login_form(page) is True

    def test_returns_false_on_timeout(self) -> None:
        page = FakePage()
        page._fail_selectors.add('input[placeholder="Username"]')
        assert has_login_form(page, timeout_ms=100) is False


class TestWelcomeText:
    def test_returns_stripped_text(self) -> None:
        page = FakePage()
        assert welcome_text(page) == "Welcome"

    def test_returns_none_on_timeout(self) -> None:
        page = FakePage()
        page._fail_selectors.add(".sapMObjectHeaderTitle, .sapMOHTitle")
        assert welcome_text(page, timeout_ms=100) is None
