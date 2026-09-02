"""Unit tests for screenshot tool (issue #686)."""

from __future__ import annotations

from fakes import ScreenshotRecordingPage

from sap_agent.context import SessionContext
from sap_agent.schemas import Config
from sap_agent.tools.screenshot import _slug, _stamp, capture_element, capture_page


def _ctx() -> SessionContext:
    return SessionContext(
        Config(app_url="http://x", username="u", password="p", artifacts_dir="/tmp/sap-test-artifacts")
    )


class TestSlug:
    def test_normalizes(self) -> None:
        assert _slug("dashboard") == "dashboard"

    def test_replaces_special_chars(self) -> None:
        assert _slug("hello world!") == "hello_world"

    def test_strips_leading_trailing_slashes(self) -> None:
        assert _slug("/orders/") == "orders"

    def test_preserves_hyphens_underscores(self) -> None:
        assert _slug("my-page_here") == "my-page_here"


class TestStamp:
    def test_contains_slug(self) -> None:
        stamp = _stamp("dashboard")
        assert "dashboard" in stamp

    def test_format(self) -> None:
        stamp = _stamp("orders")
        # Format: YYYYMMDDTHHMMSS_slug
        assert "T" in stamp
        assert len(stamp) > 15


class TestCapturePage:
    def test_returns_metadata(self) -> None:
        page = ScreenshotRecordingPage()
        ctx = _ctx()
        result = capture_page(page, "dashboard", ctx)
        assert result.route == "dashboard"
        assert result.width == 1280
        assert result.height == 800
        assert result.path.endswith(".png")

    def test_screenshot_called_full_page(self) -> None:
        page = ScreenshotRecordingPage()
        capture_page(page, "orders", _ctx())
        assert len(page.screenshot_calls) == 1
        assert page.screenshot_calls[0]["full_page"] is True


class TestCaptureElement:
    def test_returns_metadata_with_element(self) -> None:
        page = ScreenshotRecordingPage()
        result = capture_element(page, ".sapMBtn", "catalog", _ctx())
        assert result.route == "catalog"
        assert result.element == ".sapMBtn"
        assert result.width == 120
        assert result.height == 40

    def test_element_screenshot_called(self) -> None:
        page = ScreenshotRecordingPage()
        result = capture_element(page, "button", "x", _ctx())
        assert result.path.endswith(".png")
