"""QA workflow orchestrator (issue #685): audit every page of the app.

Login once, walk the route graph (dashboard -> catalog -> orders -> customer
detail), and per page: screenshot, accessibility audit, UX critique, and
performance hints from captured network. Cross-page consistency is compared
and rolled into a single QaReport.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from statistics import median
from typing import TYPE_CHECKING

from playwright.sync_api import Error as PlaywrightError

from ..schemas import QaPageReport, QaReport, Severity, UxIssue
from ..ui5.bridge import current_route
from .accessibility import audit_accessibility
from .nav import go_back, navigate, open_first_row
from .screenshot import capture_page
from .ux_critique import critique_ux

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..context import SessionContext
    from ..protocols import CaptureLike, PageLike

QA_ROUTES: tuple[str, ...] = ("dashboard", "customers", "catalog", "orders")


def _performance_hints(page: PageLike) -> list[str]:
    hints: list[str] = []
    try:
        timings = page.evaluate(
            """() => {
                const rs = performance.getEntriesByType('resource');
                return {
                    count: rs.length,
                    big: rs.filter((r) => r.transferSize > 500 * 1024).length,
                    slow: rs.filter((r) => r.duration > 3000).length,
                    largest: Math.max(0, ...rs.map((r) => r.transferSize)),
                };
            }"""
        )
    except (PlaywrightError, RuntimeError, OSError, ValueError, TypeError, AttributeError):
        return hints
    if timings.get("count", 0) > 40:
        hints.append(f"high resource count: {timings['count']} requests on this page")
    if timings.get("big", 0) > 0:
        hints.append(f"{timings['big']} resource(s) over 500KB — check image/bundle size")
    if timings.get("slow", 0) > 0:
        hints.append(f"{timings['slow']} resource(s) took >3s across the run — investigate backend latency")
    return hints


def _align_severities(report: QaPageReport) -> None:
    """Re-grade every finding via #698 rules; script severities act as fallback."""
    from .severity import classify_issue

    for issue in [*report.accessibility_issues, *report.ux_issues]:
        issue.severity = classify_issue(issue.type)


def _audit_page(page: PageLike, route: str, ctx: SessionContext, _capture: CaptureLike) -> QaPageReport:
    navigate(page, route, ctx.config.app_url, timeout_ms=ctx.config.nav_timeout_ms)
    # Viewport-only screenshots: full-page stitching is 2-4x slower and 5-10x
    # larger per route; element-level detail stays available via capture_element.
    screenshots = [capture_page(page, route, ctx, full_page=False)]
    a11y = audit_accessibility(page)
    ux = critique_ux(page)
    hints = _performance_hints(page)
    ctx.record(
        "qa",
        f"audit.{route}",
        outcome=f"screenshots={len(screenshots)} a11y={len(a11y)} ux={len(ux)}",
        url=page.url,
    )
    return QaPageReport(
        route=route,
        screenshots=screenshots,
        accessibility_issues=a11y,
        ux_issues=ux,
        performance_hints=hints,
    )


def _title_size(page: PageLike) -> float:
    try:
        return float(
            page.evaluate(
                """() => {
                    const page = Array.from(document.querySelectorAll('.sapMPage'))
                      .find((p) => p.getClientRects().length > 0 &&
                        getComputedStyle(p).display !== 'none' &&
                        getComputedStyle(p).visibility !== 'hidden');
                    const t = page && page.querySelector('.sapMTitle');
                    return t ? parseFloat(getComputedStyle(t).fontSize) : 0;
                }"""
            )
            or 0
        )
    except (PlaywrightError, RuntimeError, OSError, ValueError, TypeError, AttributeError):
        return 0.0


def _consistency_check(sizes: dict[str, float]) -> dict[str, list[UxIssue]]:
    by_route: dict[str, list[UxIssue]] = {}
    if len(sizes) < 2:
        return by_route
    values = [v for v in sizes.values() if v > 0]
    if len(values) < 2 or median(values) == 0:
        return by_route
    for route, size in sizes.items():
        if size > 0 and abs(size - median(values)) / median(values) > 0.25:
            by_route.setdefault(route, []).append(
                UxIssue(
                    type="page_consistency",
                    element=f".sapMTitle on {route}",
                    severity=Severity.MEDIUM,
                    suggestion=(
                        f"page-title font size varies ({size}px vs {int(median(values))}px median) "
                        "across pages — use one type scale"
                    ),
                )
            )
    return by_route


def run_qa(
    page: PageLike,
    capture: CaptureLike,
    ctx: SessionContext,
    *,
    app_url: str = "",
    progress: Callable[[int, int, str], None] | None = None,
    step_offset: int = 0,
) -> QaReport:
    """Audit every route; returns the assembled QaReport."""
    title_sizes: dict[str, float] = {}
    pages: list[QaPageReport] = []
    steps_total = len(QA_ROUTES) + 1  # 4 routes + customer drill-down

    for index, route in enumerate(QA_ROUTES):
        if progress:
            progress(step_offset + index + 1, step_offset + steps_total, route)
        report = _audit_page(page, route, ctx, capture)
        title_sizes[route] = _title_size(page)
        pages.append(report)

    try:
        # orders was just audited above — skip the redundant re-navigate (perf)
        if current_route(page) != "#/orders":
            navigate(page, "orders", ctx.config.app_url, timeout_ms=ctx.config.nav_timeout_ms)
        if progress:
            progress(step_offset + len(QA_ROUTES) + 1, step_offset + steps_total, "customer")
        open_first_row(page, timeout_ms=ctx.config.nav_timeout_ms)
        report = QaPageReport(
            route="customer",
            screenshots=[capture_page(page, "customer", ctx, full_page=False)],
            accessibility_issues=audit_accessibility(page),
            ux_issues=critique_ux(page),
            performance_hints=_performance_hints(page),
        )
        title_sizes["customer"] = _title_size(page)
        pages.append(report)
        go_back(page, timeout_ms=ctx.config.nav_timeout_ms)
    except (PlaywrightError, ValueError, AttributeError, KeyError, TypeError) as exc:
        ctx.record("qa", "audit.customer", f"skipped: {type(exc).__name__}")  # no detail row

    consistency = _consistency_check(dict(title_sizes))
    for report in pages:
        report.ux_issues.extend(consistency.get(report.route, []))
        _align_severities(report)

    qa_report = QaReport(
        app_url=app_url or ctx.config.app_url,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        pages=pages,
    )
    counts = qa_report.counts_by_severity()
    ctx.record(
        "qa",
        "report.summary",
        outcome=(
            f"pages={len(pages)} issues={qa_report.total_issues} "
            f"high={counts[Severity.HIGH.value]} medium={counts[Severity.MEDIUM.value]} "
            f"low={counts[Severity.LOW.value]}"
        ),
    )
    return qa_report


def write_qa_report(report: QaReport, ctx: SessionContext, *, name: str = "qa_report.json") -> str:
    """Persist the QA report next to its artifacts; returns the file path."""
    path = ctx.artifact_path(name)
    path.write_text(json.dumps(report.model_dump(), indent=2))
    return str(path)
