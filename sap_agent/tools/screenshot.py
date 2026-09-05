"""Screenshot capture tool (issue #686): full-page + element shots with metadata.

Screenshots land in `artifacts/screenshots/` with a route+timestamp name and a
typed ScreenshotResult (route, path, viewport dimensions, capture time) so the
QA report can reference them without re-reading the browser.
"""

from __future__ import annotations

import contextlib
import itertools
from datetime import UTC, datetime
from re import sub as re_sub
from typing import TYPE_CHECKING

from playwright.sync_api import Error as PlaywrightError

from ..schemas import ScreenshotResult

if TYPE_CHECKING:
    from pathlib import Path

    from ..context import SessionContext
    from ..protocols import PageLike

SCREENSHOT_DIR = "screenshots"

#: monotonic suffix so same-second captures across QA routes never collide (#perf)
_stamp_counter = itertools.count()


def _slug(value: str) -> str:
    return re_sub(r"[^A-Za-z0-9_-]+", "_", value.strip().strip("/")).strip("_")


def _stamp(route: str) -> str:
    ms = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")[:-3]
    return f"{ms}_{next(_stamp_counter):02d}_{_slug(route)}"


def _settle(page: PageLike, timeout_ms: int) -> None:
    """Fast settle: domcontentloaded is mandatory, networkidle best-effort short.

    Old code blocked up to 10s on networkidle per page (chatty Fiori = worst
    case every QA route). Domcontentloaded proves the view swapped; a short
    2s networkidle probe then catches late images without stalling the run.
    """
    page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    with contextlib.suppress(PlaywrightError):
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 2_000))


def capture_page(
    page: PageLike,
    route: str,
    ctx: SessionContext,
    *,
    timeout_ms: int | None = None,
    full_page: bool = True,
) -> ScreenshotResult:
    """Full-page screenshot of the current page; returns its metadata."""
    effective_timeout = timeout_ms if timeout_ms is not None else min(ctx.config.nav_timeout_ms, 5_000)
    _settle(page, effective_timeout)
    viewport = page.viewport_size or {"width": 0, "height": 0}
    path = _file_path(ctx, f"page_{_stamp(route)}.png")
    page.screenshot(path=str(path), full_page=full_page)
    return ScreenshotResult(
        route=route,
        path=str(path),
        width=viewport["width"],
        height=viewport["height"],
        captured_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def capture_element(
    page: PageLike, selector: str, route: str, ctx: SessionContext, *, timeout_ms: int = 10_000
) -> ScreenshotResult:
    """Screenshot of the first matching element; returns its metadata."""
    element = page.locator(selector).first
    element.wait_for(state="visible", timeout=timeout_ms)
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
