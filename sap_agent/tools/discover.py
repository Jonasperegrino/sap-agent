"""Autonomous discovery: build a structured AppSummary from DOM + network signals (#646).

Deterministic first: page title, visible tables (columns/rows), filter/form/action
controls counted by semantic selectors, OData endpoints from network capture.
Entity grouping by endpoint stem; domain from title + column heuristics.
Surfaces ranked by row count (primary data-bearing widgets first).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from playwright.sync_api import Error as PlaywrightError

from ..schemas import AppSummary, DiscoveredEntity, DiscoveredTable
from .extract import TableData, get_all_tables
from .nav import go_back, navigate, open_first_row

if TYPE_CHECKING:
    from ..context import SessionContext
    from ..protocols import CaptureLike, PageLike

#: semantic control selectors — never control ids (see D2.5)
PAGE_TITLE_SELECTOR = ".sapMPageHeader .sapMTitle, .sapMIBar-title"
FILTER_SELECTOR = ".sapMInputBase, .sapMSelect, .sapMComboBox"
FORM_SELECTOR = ".sapUiForm, .sapMForm, [data-sap-ui-formcontainer]"
ACTION_SELECTOR = ".sapMBtn:not([aria-disabled='true'])"

DOMAIN_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sales / order management", ("sales", "order", "amount", "invoice")),
    ("customer management", ("customer", "account", "contact")),
    ("inventory / logistics", ("inventory", "stock", "logistics", "delivery")),
    ("finance", ("finance", "payment", "ledger")),
    ("hr / personnel", ("employee", "personnel", "payroll")),
)


def _page_title(page: PageLike) -> str:
    candidates: list[str] = []
    for selector in PAGE_TITLE_SELECTOR.split(", "):
        locator = page.locator(selector)
        if locator.count() > 0:
            for el in locator.all():
                text = el.inner_text().strip()
                if text and text not in candidates:
                    candidates.append(text)
    # deepest match (last in DOM order) is the page title, not the shell title
    if candidates:
        return candidates[-1]
    return page.title() or ""


def _visible_texts(page: PageLike, selector: str) -> list[str]:
    texts = []
    locator = page.locator(selector)
    try:
        for el in locator.all():
            txt = el.inner_text().strip()
            if txt and txt not in texts:
                texts.append(txt)
    except (PlaywrightError, AttributeError, ValueError):  # locator stale mid-nav; best-effort
        return []
    return texts


def _entity_from_endpoint(url: str) -> str:
    match = re.search(r"/([^/]+)\.(json|odata(?:/\w+)?)$", url.rstrip("/"))
    if match:
        return match.group(1)
    return url.rstrip("/").split("/")[-1]


def _detect_domain(title: str, columns: list[str]) -> str:
    haystack = " ".join([title, *columns]).lower()
    for domain, keywords in DOMAIN_KEYWORDS:
        if any(k in haystack for k in keywords):
            return domain
    return "unknown"


@dataclass
class DiscoverResult:
    summary: AppSummary
    matched_tables: int  # tables linked to a captured endpoint


#: top-level routes the discovery walk visits (menu-driven)
_WALK_ROUTES: tuple[str, ...] = ("dashboard", "customers", "catalog", "orders")


def _walk_tables(page: PageLike, app_url: str) -> tuple[list[TableData], list[str], list[str], list[str], list[str]]:
    all_tables: list[TableData] = []
    areas: list[str] = []
    filters: list[str] = []
    forms: list[str] = []
    actions: list[str] = []

    def _accumulate() -> None:
        for txt in _visible_texts(page, FILTER_SELECTOR):
            if txt not in filters:
                filters.append(txt)
        for txt in _visible_texts(page, FORM_SELECTOR):
            if txt not in forms:
                forms.append(txt)
        for txt in _visible_texts(page, ACTION_SELECTOR):
            if txt not in actions:
                actions.append(txt)

    for route in _WALK_ROUTES:
        try:
            navigate(page, route, app_url)
        except PlaywrightError:
            continue  # best-effort walk: a broken page must not kill discovery
        areas.append(route)
        all_tables.extend(get_all_tables(page))
        _accumulate()

    try:
        navigate(page, "dashboard", app_url)
        open_first_row(page)
        areas.append("customer")
        all_tables.extend(get_all_tables(page))
        _accumulate()
        go_back(page)
    except PlaywrightError:
        pass  # no detail row present — discovery stays dashboard-level

    return all_tables, areas, filters, forms, actions


def discover_app(
    page: PageLike,
    capture: CaptureLike,
    ctx: SessionContext,
    *,
    app_url: str = "",
) -> AppSummary:
    """Run discovery once after login: walk every area, return AppSummary."""
    title = _page_title(page)
    tables, areas, filters, forms, actions = _walk_tables(page, app_url)
    endpoints = capture.capture_response_urls()

    discovered_tables: list[DiscoveredTable] = []
    matched = 0
    for idx, table in enumerate(tables):
        tbl = DiscoveredTable(
            name=f"table_{idx + 1}",
            columns=list(table.columns),
            row_count=table.row_count,
        )
        # link table to a captured endpoint by DATA overlap: any primary-key
        # value from the endpoint's JSON body appears in the rendered first column
        rendered_pks = {row[0] for row in table.rows if row}
        for url in endpoints:
            body = capture.response_body(url)
            payload = body if isinstance(body, list) else []
            if payload and isinstance(payload[0], dict):
                key = next(iter(payload[0]))
                payload_pks = {str(row.get(key, "")) for row in payload}
                if payload_pks & rendered_pks:
                    tbl.endpoint = url
                    matched += 1
                    break
        discovered_tables.append(tbl)

    # Controls were already accumulated per route during the walk (union, not
    # last-page-only); refresh on the current page in case layout changed.
    for txt in _visible_texts(page, FILTER_SELECTOR):
        if txt not in filters:
            filters.append(txt)
    for txt in _visible_texts(page, FORM_SELECTOR):
        if txt not in forms:
            forms.append(txt)
    for txt in _visible_texts(page, ACTION_SELECTOR):
        if txt not in actions:
            actions.append(txt)

    entity_names: list[str] = []
    entities: list[DiscoveredEntity] = []
    for url in endpoints:
        body = capture.response_body(url)
        if not (isinstance(body, list) and body and isinstance(body[0], dict)):
            continue  # not data (e.g. .js controller bundles) — skip
        name = _entity_from_endpoint(url)
        if url == app_url.rstrip("/") + "/" or not name:
            continue
        if name not in entity_names:
            entity_names.append(name)
            linked = [t for t in discovered_tables if t.endpoint == url]
            entities.append(DiscoveredEntity(name=name, tables=linked, endpoints=[url]))
        else:
            existing = next(e for e in entities if e.name == name)
            if url not in existing.endpoints:
                existing.endpoints.append(url)
            for t in discovered_tables:
                if t.endpoint == url and t not in existing.tables:
                    existing.tables.append(t)

    all_columns = [c for t in discovered_tables for c in t.columns]
    domain = _detect_domain(title, all_columns)
    ranked = sorted(discovered_tables, key=lambda t: t.row_count, reverse=True)

    summary = AppSummary(
        app_name=title,
        areas=areas,
        entities=entities,
        tables=discovered_tables,
        filters=filters,
        forms=forms,
        actions=actions,
        services=endpoints,
        domain=domain,
        ranked_surfaces=[t.name for t in ranked],
    )
    ctx.record(
        "discover",
        "app.summary",
        outcome=(
            f"tables={len(summary.tables)} entities={len(summary.entities)} "
            f"filters={len(summary.filters)} forms={len(summary.forms)} actions={len(summary.actions)} "
            f"services={len(summary.services)} domain={summary.domain}"
        ),
        url=page.url,
    )
    return summary
