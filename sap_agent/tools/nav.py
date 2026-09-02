"""Navigation tool (ADR D4 `nav`): menu-aware route changes and row drill-down.

The PoC is a multipage UI5 app behind a single hash router. Navigation is
semantic (menu button by text), never control-id bound — same discipline as the
`extract` tool (D2.5). Hash `page.goto` is the fallback when no menu button
exists for a route (e.g. detail pages reached by clicking a table row).
"""

from __future__ import annotations

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..ui5.bridge import current_route

#: top-level routes and their nav-bar button labels (NavBar.fragment.xml)
NAV_BUTTON_TEXTS: dict[str, str] = {
    "dashboard": "Dashboard",
    "customers": "Customers",
    "catalog": "Catalog",
    "orders": "Order History",
}

#: expected page-header title per route — only the rendered page title proves
#: the target view swapped in (navbar button text is always visible)
PAGE_TITLES: dict[str, str] = {
    "dashboard": "Sales Dashboard",
    "customers": "Customers",
    "catalog": "Product Catalog",
    "orders": "Order History",
    "customer": "Customer Details",
}

#: UI5 page title inside the *visible* page (stale views stay in the DOM hidden)
VISIBLE_PAGE_TITLE = ".sapMPage:visible .sapMTitle"


def navigate(page: Page, route: str, app_url: str, timeout_ms: int = 10_000) -> str:
    """Navigate to a top-level route (menu button first, hash goto fallback).

    Returns the resulting hash route. Raises PlaywrightTimeoutError when the
    route does not come up within the window.
    """
    expected = "#/" + route
    if current_route(page) == expected:
        return expected

    label = NAV_BUTTON_TEXTS.get(route)
    if label is not None:
        button = page.locator(f"button:has-text('{label}')").first
        try:
            button.click(timeout=timeout_ms)
        except PlaywrightTimeoutError:
            page.goto(app_url + expected, wait_until="domcontentloaded", timeout=timeout_ms)
    else:
        page.goto(app_url + expected, wait_until="domcontentloaded", timeout=timeout_ms)

    title = PAGE_TITLES.get(route)
    _wait_for_route_and_title(page, expected, title, timeout_ms)
    return current_route(page) or expected


def _wait_for_route_and_title(page: Page, expected: str, title: str | None, timeout_ms: int) -> None:
    """Combined wait: URL changed to expected route AND visible title appeared.

    Replaces the former sequential wait_for_url + _wait_for_page_title +
    _wait_for_view_settle, saving ~1s per navigation call.
    """
    if title is not None:
        expression = """([expected, title]) => {
            const urlOk = window.location.hash.includes(expected);
            if (!urlOk) return false;
            const titles = Array.from(document.querySelectorAll('.sapMPage .sapMTitle'))
                .filter(
                    (t) =>
                        t.getClientRects().length > 0 &&
                        getComputedStyle(t).display !== 'none' &&
                        getComputedStyle(t).visibility !== 'hidden'
                )
                .map((t) => t.textContent || '');
            const settleOk = Array.from(document.querySelectorAll('.sapMListTbl'))
                .filter((e) => e.offsetParent !== null).length <= 1;
            return titles.some((text) => text.includes(title)) && settleOk;
        }"""
        try:
            page.wait_for_function(expression, arg=[expected, title], timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise PlaywrightTimeoutError(
                f"route '{expected}' with title '{title}' did not appear within {timeout_ms} ms"
            ) from exc
    else:
        page.wait_for_url(lambda url: url.endswith(expected), timeout=timeout_ms)
        _wait_for_view_settle(page, timeout_ms)


def _wait_for_view_settle(page: Page, timeout_ms: int) -> None:
    """Wait until the view swap finished (≤1 visible table in the DOM).

    During the UI5 page transition the incoming and the outgoing view are both
    visible at once; table reads during that window can hit the stale page.
    """
    page.wait_for_function(
        "() => Array.from(document.querySelectorAll('.sapMListTbl')).filter(e => e.offsetParent !== null).length <= 1",
        timeout=timeout_ms,
    )


def _wait_for_page_title(page: Page, title: str, timeout_ms: int) -> None:
    """Wait until the *visible* page header shows the expected title.

    UI5 keeps every visited view in the DOM (navbar pre-renders them all),
    hiding inactive ones — a plain ``get_by_text`` match can hit a stale view,
    and navbar button labels match the target title verbatim.
    """
    expression = """(expected) => {
        const titles = Array.from(document.querySelectorAll('.sapMPage .sapMTitle'))
            .filter(
                (t) =>
                    t.getClientRects().length > 0 &&
                    getComputedStyle(t).display !== 'none' &&
                    getComputedStyle(t).visibility !== 'hidden'
            )
            .map((t) => t.textContent || '');
        return titles.some((text) => text.includes(expected));
    }"""
    try:
        page.wait_for_function(expression, arg=title, timeout=timeout_ms)
    except PlaywrightTimeoutError as exc:
        raise PlaywrightTimeoutError(f"page title '{title}' did not appear within {timeout_ms} ms") from exc


def open_first_row(page: Page, timeout_ms: int = 10_000) -> str:
    """Open the first row of the visible table → customer detail route.

    UI5 1.151 does not route synthetic DOM mouse events to list-item `press`
    (pointer-gesture delegation), so the row is opened through the control API
    (sap.m.ListBase item `firePress`) instead of a locator click (#661).
    """
    if not _press_first_visible_row(page):
        raise PlaywrightTimeoutError("no visible table row to open within the page")
    _wait_for_route_and_title(page, "#/customer/", PAGE_TITLES["customer"], timeout_ms)
    return current_route(page) or "#/customer/?"


def _press_first_visible_row(page: Page) -> bool:
    """Fire `press` on the first item of the visible sap.m.List table."""
    return bool(
        page.evaluate(
            """() => {
                const core = sap.ui.getCore();
                for (const el of document.querySelectorAll('.sapMListTbl')) {
                    if (el.offsetParent === null) continue;  // stale hidden view
                    // the <table> node id is the list ul; the control id drops
                    // the `-listUl` suffix (sap.m.Table, ListBase renderer)
                    const ctrl = core.byId(el.id.replace(/-listUl$/, ''));
                    if (ctrl && ctrl.getItems && ctrl.getItems().length) {
                        ctrl.getItems()[0].firePress();
                        return true;
                    }
                }
                return false;
            }"""
        )
    )


def go_back(page: Page, expected_route: str = "#/dashboard", timeout_ms: int = 10_000) -> str:
    """Click the Back button and wait for the expected route."""
    page.locator("button:has-text('Back')").first.click(timeout=timeout_ms)
    title = PAGE_TITLES.get(expected_route.lstrip("#/"))
    _wait_for_route_and_title(page, expected_route, title, timeout_ms)
    return current_route(page) or expected_route
