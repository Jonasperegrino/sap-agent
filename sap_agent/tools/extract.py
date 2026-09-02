"""Semantic DOM extraction from UI5 controls (architecture D4 `extract`).

Locators are semantic (control type + column headers), never hardcoded control
ids — this is what keeps the suite green across UI5 control-id renames (#652).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

TABLE_ROLE_SELECTOR = ".sapMTable, .sapMListTbl, table[role='table'], .sapUiTable"
COLUMN_HEADER_SELECTOR = "th, .sapMColumnHeader, [role='columnheader']"
# UI5 keeps previously visited pages in the DOM (hidden) — reads must scope to
# the actually visible page or they hit stale tables during the view swap
# (multipage app, #660). Each table selector is combined with a `:visible`
# page ancestor plus its own `:visible`: appending `:visible` once to the comma
# list would only constrain the last entry and still match hidden elements.
TABLE_VISIBLE_SELECTOR = ", ".join(f".sapMPage:visible {s}:visible" for s in TABLE_ROLE_SELECTOR.split(", "))


@dataclass
class TableData:
    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    row_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {"columns": self.columns, "rows": self.rows, "row_count": self.row_count}


def get_table_data(page: Page, timeout_ms: int = 15_000) -> TableData:
    """Read the first visible UI5 table on the page: columns are header texts,
    rows are the text content of each data cell."""
    try:
        table = page.locator(TABLE_VISIBLE_SELECTOR).first
        table.wait_for(state="visible", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        return TableData()

    columns = []
    for text in table.locator(COLUMN_HEADER_SELECTOR).all_inner_texts():
        text = text.strip()
        if text and text not in columns:
            columns.append(text)

    # sap.m tables put headers inside tbody — only count rows that carry `td`
    # cells, and trim each row to the detected column count so semantically
    # empty control cells (checkbox column, ObjectStatus wrappers) cannot shift
    # the data columns.
    body_rows = table.locator("tbody tr:has(td), [role='row']:has([role='cell'])")
    rows = []
    for row in body_rows.all():
        cells = [cell.strip() for cell in row.locator("td, [role='cell']").all_inner_texts() if cell.strip()]
        if cells:
            rows.append(cells[: len(columns)] if columns else cells)

    return TableData(columns=columns, rows=rows, row_count=len(rows))


def get_all_tables(page: Page, timeout_ms: int = 15_000) -> list[TableData]:
    """Read ALL visible UI5 tables on the page (discovery needs every data-bearing
    widget, not just the first). Same semantic extraction per table."""
    locator = page.locator(TABLE_VISIBLE_SELECTOR)
    try:
        locator.first.wait_for(state="visible", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        return []
    results = []
    for table in locator.all():
        columns = []
        for text in table.locator(COLUMN_HEADER_SELECTOR).all_inner_texts():
            text = text.strip()
            if text and text not in columns:
                columns.append(text)
        rows = []
        for row in table.locator("tbody tr:has(td), [role='row']:has([role='cell'])").all():
            cells = [cell.strip() for cell in row.locator("td, [role='cell']").all_inner_texts() if cell.strip()]
            if cells:
                rows.append(cells[: len(columns)] if columns else cells)
        if columns or rows:
            results.append(TableData(columns=columns, rows=rows, row_count=len(rows)))
    return results


def suggest_semantic_selector(failed_selector: str) -> str:
    """Rule-based fix suggestion for failed control-id selectors (AC6 demo).

    Detects hardcoded UI5 control ids (`__xmlview{N}--{id}`) and proposes the
    semantic table selector as replacement. A future LLM slot would map the
    failure message + DOM dump to a richer suggestion; this is the rule-based
    first step.
    """
    if "__xmlview" in failed_selector or "salesTable" in failed_selector:
        return TABLE_ROLE_SELECTOR
    return failed_selector
