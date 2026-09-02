"""Unit tests for discovery (mocked page + capture, issue #646)."""

from __future__ import annotations

import dataclasses

from fakes import FakeCapture
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from sap_agent.context import SessionContext
from sap_agent.schemas import Config
from sap_agent.tools.discover import _detect_domain, _entity_from_endpoint, discover_app
from sap_agent.tools.extract import TABLE_ROLE_SELECTOR, TABLE_VISIBLE_SELECTOR
from sap_agent.tools.nav import PAGE_TITLES, VISIBLE_PAGE_TITLE


class FakeLocator:
    def __init__(self, inner_texts: list[str], count: int = 1) -> None:
        self._inner_texts = inner_texts
        self._count = count

    def count(self) -> int:
        return self._count

    @property
    def first(self) -> FakeLocator:
        return self

    def inner_text(self) -> str:
        return self._inner_texts[0] if self._inner_texts else ""

    def all(self):
        return [FakeLocator([t]) for t in self._inner_texts]

    def all_inner_texts(self):
        return self._inner_texts

    def wait_for(self, state: str = "visible", timeout: int = 15_000) -> None:
        raise PlaywrightTimeoutError("no table")

    def click(self, timeout: int = 10_000) -> None:
        pass


class NotFoundLocator(FakeLocator):
    def __init__(self) -> None:
        super().__init__([])


class PassLocator(FakeLocator):
    def wait_for(self, state: str = "visible", timeout: int = 15_000) -> None:
        pass


@dataclasses.dataclass
class FakePage:
    title_text: str = "Sales Dashboard"
    table_locator: object = None
    page_title: str = "SAP Fiori PoC"
    url: str = "http://localhost:8080/#/dashboard"

    def title(self) -> str:
        return self.page_title

    def wait_for_url(self, url: object, timeout: int = 10_000) -> None:
        pass

    def wait_for_function(self, expression: str, arg=None, timeout: int = 10_000) -> None:
        pass

    def evaluate(self, expression: str) -> bool:
        return True

    def get_by_text(self, text: str, exact: bool = False) -> PassLocator:
        return PassLocator([text])

    def locator(self, selector: str) -> FakeLocator:
        if selector in (".sapMIBar-title", ".sapMTitle"):
            return FakeLocator([self.title_text])
        if selector == VISIBLE_PAGE_TITLE:
            return FakeLocator(list(PAGE_TITLES.values()))
        if selector in (TABLE_ROLE_SELECTOR, TABLE_VISIBLE_SELECTOR):
            return self.table_locator if self.table_locator is not None else NotFoundLocator()
        if selector == ".sapMShellAppWidthLimited, .sapMTB, .sapUshellShellContainer":
            return FakeLocator(["Shell Area"], 1)
        return FakeLocator([])


class FakeTableList:
    def __init__(self, count: int = 1) -> None:
        self._count = count

    @property
    def first(self):
        return self

    def wait_for(self, state: str = "visible", timeout: int = 15_000) -> None:
        pass

    def all(self):
        return [FakeTable(), FakeTable2()] if self._count > 1 else [FakeTable()]


class FakeTableRow:
    def __init__(self) -> None:
        pass

    def locator(self, selector: str):
        return FakeCellList()


class FakeCellList:
    def all_inner_texts(self):
        return ["SO-1001\n", "Acme Corp\n", "€12,450.00\n", "Approved\n"]


class FakeRowList:
    def all(self):
        return [FakeTableRow(), FakeTableRow()]


class FakeTable:
    def locator(self, selector: str):
        if "th" in selector or "columnheader" in selector:
            return FakeLocator(["Order ID\n", "Customer\n", "Amount\n", "Status\n", "Built\n"], 5)
        return FakeRowList()


class FakeTable2:
    def locator(self, selector: str):
        if "th" in selector or "columnheader" in selector:
            return FakeLocator(["Month\n", "Revenue\n"], 2)
        return FakeRowList()


def _ctx() -> SessionContext:
    return SessionContext(Config(app_url="http://localhost:8080", username="demo", password="x"))


class TestEntityExtraction:
    def test_stem_from_json(self) -> None:
        assert _entity_from_endpoint("http://localhost:8080/data/sales.json") == "sales"

    def test_stem_from_odata(self) -> None:
        assert _entity_from_endpoint("http://sap/OData/SalesOrderSet") == "SalesOrderSet"

    def test_domain_detection(self) -> None:
        assert _detect_domain("Sales Dashboard", ["Order ID", "Amount"]) == "sales / order management"
        assert _detect_domain("HR Portal", ["Employee"]) == "hr / personnel"
        assert _detect_domain("Mystery", ["Xyz"]) == "unknown"


class TestDiscoverAppUnit:
    # the discovery walk visits dashboard/catalog/orders + one customer detail;
    # the fake page serves the same table on every area, so each visit collects
    # the mock tables again
    AREAS = 4

    def test_summary_shape(self) -> None:
        page = FakePage(table_locator=FakeTableList())
        summary = discover_app(
            page,
            FakeCapture(["http://localhost:8080/data/sales.json"]),
            _ctx(),
            app_url="http://localhost:8080",
        )
        assert summary.app_name == "Sales Dashboard"
        assert summary.domain == "sales / order management"
        assert len(summary.tables) == self.AREAS
        assert summary.tables[0].columns == ["Order ID", "Customer", "Amount", "Status", "Built"]
        assert summary.tables[0].row_count == 2
        assert summary.tables[0].endpoint == "http://localhost:8080/data/sales.json"
        assert [e.name for e in summary.entities] == ["sales"]
        assert summary.services == ["http://localhost:8080/data/sales.json"]
        assert summary.areas == ["dashboard", "catalog", "orders", "customer"]

    def test_multiple_tables_all_discovered_and_ranked(self) -> None:
        page = FakePage(table_locator=FakeTableList(count=2))
        summary = discover_app(page, FakeCapture([]), _ctx(), app_url="http://localhost:8080")
        assert len(summary.tables) == self.AREAS * 2
        assert summary.ranked_surfaces == [f"table_{i}" for i in range(1, self.AREAS * 2 + 1)]

    def test_no_tables_does_not_crash(self) -> None:
        page = FakePage(title_text="Empty", table_locator=None)
        summary = discover_app(page, FakeCapture([]), _ctx(), app_url="http://localhost:8080")
        assert summary.tables == []
        assert summary.domain == "unknown"
