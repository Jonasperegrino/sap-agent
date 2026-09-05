"""Extended tests for QA _audit_page and run_qa (issue #685)."""

from __future__ import annotations

from unittest.mock import patch

from fakes import PageStub
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from sap_agent.context import SessionContext
from sap_agent.schemas import Config, QaPageReport, ScreenshotResult
from sap_agent.tools.qa import _audit_page, run_qa


def fake_screenshot(route: str = "x") -> ScreenshotResult:
    return ScreenshotResult(route=route, path="s.png", width=1280, height=800)


class FakePage(PageStub):
    def __init__(self) -> None:
        self.url = "http://localhost:8080/#/dashboard"
        self._eval_results: list[object] = []

    def evaluate(self, expression: str, arg=None, **kwargs):
        if self._eval_results:
            return self._eval_results.pop(0)
        return None


class FakeCapture:
    def __init__(self) -> None:
        self._urls: list[str] = []

    def capture_response_urls(self, url_substring: str | None = None) -> list[str]:
        if url_substring:
            return [u for u in self._urls if url_substring in u]
        return self._urls

    def latest_response_body(self, url_substring: str):
        return None

    def response_body(self, url: str):
        return None


def _ctx() -> SessionContext:
    cfg = Config(app_url="http://x", username="u", password="p")
    return SessionContext(cfg)


@patch("sap_agent.tools.qa._performance_hints", return_value=["hint1"])
@patch("sap_agent.tools.qa.critique_ux", return_value=[])
@patch("sap_agent.tools.qa.audit_accessibility", return_value=[])
@patch("sap_agent.tools.qa.capture_page", return_value=fake_screenshot("dashboard"))
@patch("sap_agent.tools.qa.navigate")
def test_audit_page_calls_all_tools(mock_nav, mock_cap, mock_a11y, mock_ux, mock_perf) -> None:
    page = FakePage()
    ctx = _ctx()
    capture = FakeCapture()
    report = _audit_page(page, "dashboard", ctx, capture)
    assert isinstance(report, QaPageReport)
    assert report.route == "dashboard"
    mock_nav.assert_called_once()
    mock_cap.assert_called_once()
    mock_a11y.assert_called_once()
    mock_ux.assert_called_once()
    mock_perf.assert_called_once()


@patch("sap_agent.tools.qa._performance_hints", return_value=[])
@patch("sap_agent.tools.qa.critique_ux", return_value=[])
@patch("sap_agent.tools.qa.audit_accessibility", return_value=[])
@patch("sap_agent.tools.qa.capture_page", return_value=fake_screenshot())
@patch("sap_agent.tools.qa._align_severities")
@patch("sap_agent.tools.qa.open_first_row", side_effect=PlaywrightTimeoutError("no row"))
@patch("sap_agent.tools.qa.go_back")
@patch("sap_agent.tools.qa.navigate")
def test_run_qa_covers_all_routes(
    mock_nav, mock_go_back, mock_open, mock_align, mock_cap, mock_a11y, mock_ux, mock_perf
) -> None:
    page = FakePage()
    ctx = _ctx()
    capture = FakeCapture()
    report = run_qa(page, capture, ctx, app_url="http://x")
    assert len(report.pages) >= 3  # dashboard, catalog, orders + customer attempt
    assert mock_nav.call_count >= 3


@patch("sap_agent.tools.qa._performance_hints", return_value=["slow"])
@patch("sap_agent.tools.qa.critique_ux", return_value=[])
@patch("sap_agent.tools.qa.audit_accessibility", return_value=[])
@patch("sap_agent.tools.qa.capture_page", return_value=fake_screenshot())
@patch("sap_agent.tools.qa._align_severities")
@patch("sap_agent.tools.qa.navigate")
def test_run_qa_progress_callback(mock_nav, mock_align, mock_cap, mock_a11y, mock_ux, mock_perf) -> None:
    page = FakePage()
    ctx = _ctx()
    capture = FakeCapture()
    calls = []
    run_qa(page, capture, ctx, progress=lambda step, total, route: calls.append((step, total, route)))
    assert len(calls) > 0
    assert calls[0][2] == "dashboard"


@patch("sap_agent.tools.qa._performance_hints", return_value=[])
@patch("sap_agent.tools.qa.critique_ux", return_value=[])
@patch("sap_agent.tools.qa.audit_accessibility", return_value=[])
@patch("sap_agent.tools.qa.capture_page", return_value=fake_screenshot())
@patch("sap_agent.tools.qa._align_severities")
@patch("sap_agent.tools.qa.open_first_row")
@patch("sap_agent.tools.qa.go_back")
@patch("sap_agent.tools.qa.navigate")
def test_run_qa_customer_detail_success(
    mock_nav, mock_go_back, mock_open, mock_align, mock_cap, mock_a11y, mock_ux, mock_perf
) -> None:
    page = FakePage()
    ctx = _ctx()
    capture = FakeCapture()
    report = run_qa(page, capture, ctx, app_url="http://x")
    # Should have 4 pages: dashboard, catalog, orders, customer
    routes = [p.route for p in report.pages]
    assert "customer" in routes
