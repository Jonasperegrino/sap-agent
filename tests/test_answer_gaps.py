"""Gap tests for the answer split (issue #693 follow-up): snapshot paths.

Network-first paths are covered elsewhere; these pin the table-snapshot
fallbacks, the shared fetch helper, and the join builder.
"""

from __future__ import annotations

from fakes import FakeCapture, FakeTablePage, PageStub

from sap_agent.context import SessionContext
from sap_agent.schemas import Config, IntentConfig, QuestionIntent
from sap_agent.tools.answer_aggregate import _aggregate_top, _customer_join
from sap_agent.tools.answer_core import _TableSnapshot, fetch_json_body
from sap_agent.tools.answer_lookup import _lookup_customer, _lookup_product
from sap_agent.tools.extract import TableData


def _ctx() -> SessionContext:
    return SessionContext(Config(app_url="http://x", username="u", password="p"))


def _intent(**kw) -> IntentConfig:
    base = {"intent": QuestionIntent.AGGREGATE, "comparer": "exact"}
    base.update(kw)
    return IntentConfig(**base)


class TestFetchJsonBody:
    def test_latest_hit(self) -> None:
        cap = FakeCapture(["http://x/sales.json"], bodies={"sales.json": [{"a": 1}]})
        assert fetch_json_body(cap, "sales.json") == [{"a": 1}]

    def test_url_scan_hit(self) -> None:
        cap = FakeCapture(["http://x/data/sales.json"], bodies={"other": [{"a": 1}], "sales.json": [{"b": 2}]})
        # latest misses (no url contains stem match on latest? latest IS the sales url)
        assert fetch_json_body(cap, "sales.json") == [{"b": 2}]

    def test_none_capture(self) -> None:
        assert fetch_json_body(None, "sales.json") is None

    def test_non_list_body(self) -> None:
        cap = FakeCapture(["http://x/sales.json"], bodies={"sales.json": {"a": 1}})
        assert fetch_json_body(cap, "sales.json") is None

    def test_no_match(self) -> None:
        cap = FakeCapture(["http://x/other.json"], bodies={"other": [{"a": 1}]})
        assert fetch_json_body(cap, "nope.json") is None


class TestCustomerJoin:
    def test_maps_id_and_name(self) -> None:
        cap = FakeCapture(
            ["http://x/customers.json"],
            bodies={"customers.json": [{"id": "C-1", "name": "Acme Corp", "country": "Germany"}]},
        )
        by_id, by_name = _customer_join(cap, "customers.json", "country")
        assert by_id == {"C-1": "Germany"}
        assert by_name == {"acme corp": "Germany"}

    def test_none_capture(self) -> None:
        assert _customer_join(None, "customers.json", "country") == ({}, {})


def _snapshot(columns: list[str], rows: list[list[str]]) -> _TableSnapshot:
    data = TableData(columns=columns, rows=rows, row_count=len(rows))
    return _TableSnapshot(data=data, columns={name: idx for idx, name in enumerate(columns)})


class TestAggregateSnapshot:
    def test_count_by_status(self) -> None:
        snap = _snapshot(
            ["Order", "Status"],
            [["SO-1", "Approved"], ["SO-2", "Pending"], ["SO-3", "Approved"]],
        )
        result = _aggregate_top(
            "q",
            _intent(aggregation="count", group_by="status"),
            source="salesTable",
            endpoint=None,
            snapshot=snap,
        )
        assert result is not None
        assert result.answer == [{"status": "Approved", "count": 2}, {"status": "Pending", "count": 1}]

    def test_sum_revenue(self) -> None:
        snap = _snapshot(
            ["Customer", "Amount"],
            [["Acme", "100"], ["Acme", "50"], ["Globex", "25"]],
        )
        result = _aggregate_top(
            "q",
            _intent(aggregation="sum", aggregation_column="amount", group_by="customer"),
            source="salesTable",
            endpoint=None,
            snapshot=snap,
        )
        assert result is not None
        assert result.answer == [
            {"customer": "Acme", "revenue": 150.0, "amount": 150.0},
            {"customer": "Globex", "revenue": 25.0, "amount": 25.0},
        ]

    def test_avg_no_group(self) -> None:
        snap = _snapshot(["Amount"], [["10"], ["20"]])
        result = _aggregate_top(
            "q",
            _intent(aggregation="avg", aggregation_column="amount"),
            source="salesTable",
            endpoint=None,
            snapshot=snap,
        )
        assert result is not None
        assert result.answer == [{"average": 15.0}]

    def test_empty_is_not_found(self) -> None:
        snap = _snapshot(["Status"], [])
        result = _aggregate_top(
            "q",
            _intent(aggregation="count", group_by="status"),
            source="salesTable",
            endpoint=None,
            snapshot=snap,
        )
        assert result is not None
        assert result.not_found

    def test_empty_group_yields_not_found(self) -> None:
        snap = _snapshot(["Status"], [])
        result = _aggregate_top(
            "q",
            _intent(aggregation="sum", aggregation_column="amount", group_by="status", column=None),
            source="salesTable",
            endpoint=None,
            snapshot=snap,
        )
        assert result is not None
        assert result.not_found


class TestLookupTableFallback:
    def test_customer_contact_via_table(self) -> None:
        class ContactLocator:
            def __init__(self, selector: str) -> None:
                self._selector = selector

            @property
            def first(self):
                return self

            def wait_for(self, state: str = "visible", timeout: int = 15_000) -> None:
                pass

            def locator(self, selector: str):
                return ContactLocator(selector)

            def all_inner_texts(self):
                if "th" in self._selector or "columnheader" in self._selector:
                    return ["Customer\n", "Contact\n"]
                if "td" in self._selector or "cell" in self._selector:
                    return ["Acme Corp\n", "Jane Doe\n"]
                return []

            def all(self):
                return [ContactRow()]

            def click(self, timeout: int | None = None) -> None:
                pass

        class ContactRow:
            def locator(self, selector: str):
                class Cells:
                    def all_inner_texts(self):
                        return ["Acme Corp\n", "Jane Doe\n"]

                return Cells()

        class ContactPage(PageStub):
            url = "http://localhost:8080/#/customers"

            def locator(self, selector: str):
                return ContactLocator(selector)

        ctx = _ctx()
        intent = IntentConfig(intent=QuestionIntent.LOOKUP, column="contact", value="Acme Corp", comparer="exact")
        result = _lookup_customer("who is contact at Acme Corp?", intent, ctx, ContactPage(), "http://x", None)
        assert result is not None
        assert result.answer == [{"customer": "Acme Corp", "contact": "Jane Doe", "email": "", "phone": ""}]

    def test_product_lookup_unknown_column(self) -> None:
        ctx = _ctx()
        intent = IntentConfig(intent=QuestionIntent.LOOKUP, column="frobnicate", value="Pump", comparer="exact")
        page = FakeTablePage([])
        assert _lookup_product("price of Pump?", intent, ctx, page, "http://x", None) is None

    def test_product_not_found_via_network(self) -> None:
        cap = FakeCapture(["http://x/products.json"], bodies={"products.json": [{"name": "Pump P-1"}]})
        ctx = _ctx()
        intent = IntentConfig(intent=QuestionIntent.LOOKUP, column="price", value="Nope", comparer="exact")
        page = FakeTablePage([])
        result = _lookup_product("price of Nope?", intent, ctx, page, "http://x", cap)
        assert result is not None
        assert result.not_found
