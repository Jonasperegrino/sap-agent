"""Unit tests for network capture and table extraction (mock page/responses)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fakes import FakeResponse, FakeTablePage, PageStub

from sap_agent.tools.extract import TableData, get_table_data
from sap_agent.tools.network import NetworkCapture

if TYPE_CHECKING:
    from collections.abc import Callable

FIXTURE = [
    {"id": "SO-1001", "customer": "Acme Corp", "amount": "€12,450.00", "status": "Approved"},
    {"id": "SO-1002", "customer": "GlobalTech", "amount": "€8,230.00", "status": "Pending"},
]


class EmittingPage(PageStub):
    """Network-fake page: registers the response handler, replays canned responses."""

    def __init__(self, responses: list[tuple[str, object]]) -> None:
        self._handler: Callable[..., Any] | None = None
        self._responses = responses

    def on(self, event: str, f: Callable[..., None]) -> None:
        if event != "response":
            raise AssertionError(f"unexpected event {event}")
        self._handler = f

    def emit(self) -> None:
        assert self._handler is not None, "no response handler registered"
        for url, body in self._responses:
            self._handler(FakeResponse(url, body))


class TestNetworkCaptureUnit:
    def test_records_only_same_origin(self) -> None:
        page = EmittingPage(
            [
                ("http://localhost:8080/data/sales.json", FIXTURE),
                ("https://sdk.openui5.org/resources/sap-ui-core.js", {}),
            ]
        )
        capture = NetworkCapture(page, "http://localhost:8080")
        page.emit()
        assert capture.capture_response_urls() == ["http://localhost:8080/data/sales.json"]

    def test_filter_by_substring(self) -> None:
        page = EmittingPage([("http://localhost:8080/data/sales.json", FIXTURE)])
        capture = NetworkCapture(page, "http://localhost:8080")
        page.emit()
        assert capture.capture_response_urls("sales.json") == ["http://localhost:8080/data/sales.json"]
        assert capture.capture_response_urls("nope") == []

    def test_latest_response_body(self) -> None:
        page = EmittingPage([("http://localhost:8080/data/sales.json", FIXTURE)])
        capture = NetworkCapture(page, "http://localhost:8080")
        page.emit()
        assert capture.latest_response_body("sales.json") == FIXTURE
        assert capture.latest_response_body("missing") is None

    def test_matches_fixture(self) -> None:
        page = EmittingPage([("http://localhost:8080/data/sales.json", FIXTURE)])
        capture = NetworkCapture(page, "http://localhost:8080")
        page.emit()
        assert capture.matches_fixture("sales.json", FIXTURE)
        assert not capture.matches_fixture("sales.json", [{"x": 1}])

    def test_non_json_body_ignored(self) -> None:
        class NonJsonResponse(FakeResponse):
            def json(self) -> object:
                raise ValueError("not json")

        page = EmittingPage([])  # emit manually below
        capture = NetworkCapture(page, "http://localhost:8080")
        assert page._handler is not None  # type: ignore[attr-defined]
        page._handler(NonJsonResponse("http://localhost:8080/plain.txt", "text"))  # type: ignore[attr-defined]
        assert capture.capture_response_urls() == ["http://localhost:8080/plain.txt"]
        assert capture.latest_response_body("plain.txt") is None


_FIXTURE_ROWS = [
    ["SO-1001", "Acme Corp", "€12,450.00", "Approved", "2026-01-15"],
    ["SO-1004", "Nordic Supply", "€21,100.00", "Approved", "2026-04-01"],
]


class TestTableExtractionUnit:
    def test_empty_page_returns_empty_table(self) -> None:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        class NotFoundPage(PageStub):
            def locator(self, selector):
                class NotFound:
                    @property
                    def first(self):
                        return self

                    def wait_for(self, state="visible", timeout=15000):
                        raise PlaywrightTimeoutError("no table")

                return NotFound()

        data = get_table_data(NotFoundPage())
        assert data == TableData()

    def test_columns_and_rows_extracted(self) -> None:
        data = get_table_data(FakeTablePage(_FIXTURE_ROWS))
        assert data.columns == ["Order ID", "Customer", "Amount", "Status", "Built"]
        assert data.row_count == 2
        assert data.rows[0][:2] == ["SO-1001", "Acme Corp"]

    def test_response_body_exact_url(self) -> None:
        page = EmittingPage([("http://localhost:8080/data/sales.json", FIXTURE)])
        capture = NetworkCapture(page, "http://localhost:8080")
        page.emit()
        assert capture.response_body("http://localhost:8080/data/sales.json") == FIXTURE
        assert capture.response_body("http://localhost:8080/data/nope.json") is None

    def test_response_payloads_returns_all(self) -> None:
        page = EmittingPage(
            [
                ("http://localhost:8080/data/sales.json", FIXTURE),
                ("http://localhost:8080/data/products.json", [{"id": 1}]),
            ]
        )
        capture = NetworkCapture(page, "http://localhost:8080")
        page.emit()
        payloads = capture.response_payloads()
        assert len(payloads) == 2
        assert "http://localhost:8080/data/sales.json" in payloads
        assert "http://localhost:8080/data/products.json" in payloads

    def test_latest_wins_on_duplicate_url(self) -> None:
        page = EmittingPage(
            [
                ("http://localhost:8080/data/sales.json", [FIXTURE[0]]),
                ("http://localhost:8080/data/sales.json", FIXTURE),
            ]
        )
        capture = NetworkCapture(page, "http://localhost:8080")
        page.emit()
        assert capture.latest_response_body("sales.json") == FIXTURE
        assert len(capture.capture_response_urls("sales.json")) == 2

    def test_empty_capture(self) -> None:
        page = EmittingPage([])
        capture = NetworkCapture(page, "http://localhost:8080")
        assert capture.capture_response_urls() == []
        assert capture.latest_response_body("anything") is None
        assert capture.response_payloads() == {}


class TestSuggestSemanticSelector:
    def test_hardcoded_id_gets_semantic_replacement(self) -> None:
        from sap_agent.tools.extract import TABLE_ROLE_SELECTOR, suggest_semantic_selector

        assert suggest_semantic_selector("#__xmlview1--salesTable-listUl") == TABLE_ROLE_SELECTOR
        assert "salesTable" not in suggest_semantic_selector("#__xmlview1--salesTable-listUl")

    def test_already_semantic_selector_unchanged(self) -> None:
        from sap_agent.tools.extract import suggest_semantic_selector

        assert suggest_semantic_selector(".sapMListTbl") == ".sapMListTbl"


class TestNetworkCaptureLru:
    def test_evicts_oldest_beyond_max(self) -> None:
        capture = NetworkCapture(EmittingPage([]), "http://x", max_bodies=2)
        capture._store_body("http://x/a", {"i": 1})
        capture._store_body("http://x/b", {"i": 2})
        capture._store_body("http://x/c", {"i": 3})

        assert capture.response_body("http://x/a") is None
        assert capture.response_body("http://x/b") == {"i": 2}
        assert capture.response_body("http://x/c") == {"i": 3}

    def test_access_refreshes_recency(self) -> None:
        page = EmittingPage([("http://x/a", {"i": 1}), ("http://x/b", {"i": 2})])
        capture = NetworkCapture(page, "http://x", max_bodies=2)
        page.emit()
        assert capture.latest_response_body("/a") == {"i": 1}

        capture._store_body("http://x/c", {"i": 3})

        assert capture.response_body("http://x/a") == {"i": 1}
        assert capture.response_body("http://x/b") is None

    def test_same_url_update_keeps_single_entry(self) -> None:
        capture = NetworkCapture(EmittingPage([]), "http://x", max_bodies=2)
        capture._store_body("http://x/a", {"v": 1})
        capture._store_body("http://x/b", {"v": 2})
        capture._store_body("http://x/a", {"v": 10})
        capture._store_body("http://x/c", {"v": 3})

        assert capture.response_body("http://x/a") == {"v": 10}
        assert capture.response_body("http://x/b") is None
        assert capture.response_body("http://x/c") == {"v": 3}
