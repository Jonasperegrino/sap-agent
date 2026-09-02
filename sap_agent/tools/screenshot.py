"""Screenshot capture tool (issue #686): full-page + element shots with metadata.

Screenshots land in `artifacts/screenshots/` with a route+timestamp name and a
typed ScreenshotResult (route, path, viewport dimensions, capture time) so the
QA report can reference them without re-reading the browser.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from re import sub as re_sub

from playwright.sync_api import Page

from ..context import SessionContext
from ..schemas import ScreenshotResult

SCREENSHOT_DIR = "screenshots"


def _slug(value: str) -> str:
    return re_sub(r"[^A-Za-z0-9_-]+", "_", value.strip().strip("/")).strip("_")


def _stamp(route: str) -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "_" + _slug(route)


def capture_page(page: Page, route: str, ctx: SessionContext) -> ScreenshotResult:
    """Full-page screenshot of the current page; returns its metadata."""
    page.wait_for_load_state("networkidle", timeout=10_000)
    viewport = page.viewport_size or {"width": 0, "height": 0}
    path = _file_path(ctx, f"page_{_stamp(route)}.png")
    page.screenshot(path=str(path), full_page=True)
    return ScreenshotResult(
        route=route,
        path=str(path),
        width=viewport["width"],
        height=viewport["height"],
        captured_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def capture_element(page: Page, selector: str, route: str, ctx: SessionContext) -> ScreenshotResult:
    """Screenshot of the first matching element; returns its metadata."""
    element = page.locator(selector).first
    element.wait_for(state="visible", timeout=10_000)
    box = element.bounding_box() or {"width": 0, "height": 0}
    path = _file_path(ctx, f"element_{_stamp(route)}_{_slug(selector)}.png")
    element.screenshot(path=str(path))
    return ScreenshotResult(
        route=route,
        path=str(path),
        width=int(box["width"]),
        height=int(box["height"]),
        captured_at=datetime.now(UTC).isoformat(timespec="seconds"),
        element=selector,
    )


def _file_path(ctx: SessionContext, name: str) -> Path:
    directory = ctx.artifact_path(SCREENSHOT_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / name
