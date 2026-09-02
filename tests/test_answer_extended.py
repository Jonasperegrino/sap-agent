"""Extended unit tests for answer tool — more branches (issue #651)."""

from __future__ import annotations

import dataclasses

from fakes import FakeCapture, FakeLocator

from sap_agent.context import SessionContext
from sap_agent.schemas import Config, IntentConfig, QuestionIntent
from sap_agent.tools.answer import (
    _checksum,
    _matches,
    _normalize,
    _parse_amount,
    _resolve_json_key,
    evaluate_question,
)

ROWS: list[list[str]] = [
    ["SO-1001", "Acme Corp", "12450", "Approved\nObject Status", "2026-01-15"],
    ["SO-1002", "GlobalTech", "8230", "Pending\nObject Status", "2026-02-03"],
    ["SO-1004", "Nordic Supply", "21100", "Approved\nObject Status", "2026-04-01"],
]


@dataclasses.dataclass
class FakePage:
    url: str = "http://localhost:8080/#/dashboard"
    rows: list[list[str]] = dataclasses.field(default_factory=lambda: ROWS)

    def locator(self, selector: str):
        return FakeLocator(selector, self.rows)


def _ctx() -> SessionContext:
    return SessionContext(Config(app_url="http://localhost:8080", username="demo", password="x"))


class TestNormalize:
    def test_strips_object_status_noise(self) -> None:
        assert _normalize("Approved\nObject Status\nEntry") == "Approved"

    def test_plain_text(self) -> None:
        assert _normalize("hello") == "hello"

    def test_strips_whitespace(self) -> None:
        assert _normalize("  foo  ") == "foo"


class TestParseAmount:
    def test_numeric_passthrough(self) -> None:
        assert _parse_amount(42.5) == 42.5
        assert _parse_amount(100) == 100.0

    def test_euro_string(self) -> None:
        assert _parse_amount("€12,450.00") == 12450.0

    def test_plain_string(self) -> None:
        assert _parse_amount("8230") == 8230.0

    def test_empty_string(self) -> None:
        assert _parse_amount("") == 0.0

    def test_none(self) -> None:
        assert _parse_amount(None) == 0.0

    def test_garbage(self) -> None:
        assert _parse_amount("abc") == 0.0


class TestResolveJsonKey:
    def test_known_columns(self) -> None:
        assert _resolve_json_key("amount") == "amountEur"
        assert _resolve_json_key("customer") == "customer"
        assert _resolve_json_key("built") == "built"

    def test_unknown_column(self) -> None:
        assert _resolve_json_key("foobar") == "foobar"

    def test_none_column(self) -> None:
        assert _resolve_json_key(None) == ""


class TestMatches:
    def test_exact_match(self) -> None:
        assert _matches("Approved", "Approved", "exact") is True

    def test_case_insensitive(self) -> None:
        assert _matches("Approved", "approved", "exact") is True

    def test_year_match(self) -> None:
        assert _matches("2026-01-15", "2026", "year") is True

    def test_year_no_match(self) -> None:
        assert _matches("2025-01-15", "2026", "year") is False

    def test_year_short_cell(self) -> None:
        assert _matches("ab", "2026", "year") is False

    def test_object_status_normalized(self) -> None:
        assert _matches("Approved\nObject Status", "Approved", "exact") is True


class TestChecksum:
    def test_deterministic(self) -> None:
        from sap_agent.schemas import AnsweredQuestion, AnswerEvidence

        q = AnsweredQuestion(
            question="test",
            intent=QuestionIntent.COUNT_TOTAL,
            answer=5,
            evidence=AnswerEvidence(source="s", matched_rows=5),
        )
        c1 = _checksum(q)
        c2 = _checksum(q)
        assert c1 == c2
        assert len(c1) == 16


class TestEvaluateQuestionBranches:
    def test_unsupported_intent(self) -> None:
        ctx = _ctx()
        intent = IntentConfig(intent=QuestionIntent.UNSUPPORTED, follow_up="unsupported question type")
        result = evaluate_question(FakePage(), "play jazz", ctx, intent=intent)
        assert result.unsupported is True

    def test_count_total(self) -> None:
        ctx = _ctx()
        intent = IntentConfig(intent=QuestionIntent.COUNT_TOTAL)
        result = evaluate_question(FakePage(), "how many orders?", ctx, intent=intent)
        assert result.answer == 3
        assert result.intent == QuestionIntent.COUNT_TOTAL

    def test_count_where(self) -> None:
        ctx = _ctx()
        intent = IntentConfig(intent=QuestionIntent.COUNT_WHERE, column="Status", value="Approved", comparer="exact")
        result = evaluate_question(FakePage(), "how many approved?", ctx, intent=intent)
        assert result.answer == 2

    def test_existence_found(self) -> None:
        ctx = _ctx()
        intent = IntentConfig(intent=QuestionIntent.EXISTENCE, column="Status", value="Approved")
        result = evaluate_question(FakePage(), "is there any approved?", ctx, intent=intent)
        assert result.answer == 1

    def test_column_not_in_table(self) -> None:
        ctx = _ctx()
        intent = IntentConfig(intent=QuestionIntent.COUNT_WHERE, column="Nonexistent", value="x")
        result = evaluate_question(FakePage(), "count x", ctx, intent=intent)
        assert result.unsupported is True
        assert "not present" in result.message

    def test_value_required_for_filter(self) -> None:
        ctx = _ctx()
        intent = IntentConfig(intent=QuestionIntent.COUNT_WHERE, column="Status")
        result = evaluate_question(FakePage(), "count status", ctx, intent=intent)
        assert result.unsupported is True
        assert "need a value" in result.message

    def test_column_and_value_required(self) -> None:
        ctx = _ctx()
        intent = IntentConfig(intent=QuestionIntent.COUNT_WHERE)
        result = evaluate_question(FakePage(), "count", ctx, intent=intent)
        assert result.unsupported is True
        assert "column and value are required" in result.message

    def test_not_found(self) -> None:
        ctx = _ctx()
        intent = IntentConfig(intent=QuestionIntent.COUNT_WHERE, column="Status", value="Purple")
        result = evaluate_question(FakePage(), "count purple", ctx, intent=intent)
        assert result.not_found is True
        assert result.answer is None

    def test_aggregate_unsupported_without_data(self) -> None:
        ctx = _ctx()
        intent = IntentConfig(
            intent=QuestionIntent.AGGREGATE,
            aggregation="sum",
            aggregation_column="amount",
            group_by="customer",
        )
        # With no network rows and no snapshot data matching, falls back to table
        result = evaluate_question(FakePage(), "revenue by customer", ctx, intent=intent)
        assert result.intent == QuestionIntent.AGGREGATE
        # Table fallback produces results from the FakePage rows
        assert isinstance(result.answer, list)

    def test_aggregate_with_network_rows(self) -> None:
        from fakes import FakeCapture

        ctx = _ctx()
        intent = IntentConfig(
            intent=QuestionIntent.AGGREGATE,
            aggregation="sum",
            aggregation_column="amount",
            group_by="customer",
            limit=2,
        )
        network_rows = [
            {"customer": "Acme Corp", "amountEur": 12450},
            {"customer": "GlobalTech", "amountEur": 8230},
            {"customer": "Acme Corp", "amountEur": 5000},
        ]
        capture = FakeCapture(["http://x/sales.json"], bodies={"sales.json": network_rows})
        result = evaluate_question(FakePage(), "top 2 customers", ctx, intent=intent, capture=capture)
        assert result.intent == QuestionIntent.AGGREGATE
        assert isinstance(result.answer, list)
        assert len(result.answer) == 2

    def test_aggregate_with_network_rows_filter(self) -> None:
        from fakes import FakeCapture

        ctx = _ctx()
        intent = IntentConfig(
            intent=QuestionIntent.AGGREGATE,
            column="status",
            value="Approved",
            aggregation="sum",
            aggregation_column="amount",
            group_by="customer",
        )
        network_rows = [
            {"status": "Approved", "customer": "Acme Corp", "amountEur": 12450},
            {"status": "Pending", "customer": "GlobalTech", "amountEur": 8230},
        ]
        capture = FakeCapture(["http://x/sales.json"], bodies={"sales.json": network_rows})
        result = evaluate_question(FakePage(), "revenue approved", ctx, intent=intent, capture=capture)
        assert result.intent == QuestionIntent.AGGREGATE
        assert isinstance(result.answer, list)
        # Only Acme Corp has status=Approved
        assert len(result.answer) == 1
        assert result.answer[0]["customer"] == "Acme Corp"

    def test_aggregate_network_rows_empty_after_filter(self) -> None:
        from fakes import FakeCapture

        ctx = _ctx()
        intent = IntentConfig(
            intent=QuestionIntent.AGGREGATE,
            column="status",
            value="Nonexistent",
            aggregation="sum",
            aggregation_column="amount",
            group_by="customer",
        )
        network_rows = [
            {"status": "Approved", "customer": "Acme Corp", "amountEur": 12450},
        ]
        capture = FakeCapture(["http://x/sales.json"], bodies={"sales.json": network_rows})
        result = evaluate_question(FakePage(), "revenue nonexistent", ctx, intent=intent, capture=capture)
        assert result.not_found is True

    def test_aggregate_table_fallback(self) -> None:
        ctx = _ctx()
        intent = IntentConfig(
            intent=QuestionIntent.AGGREGATE,
            aggregation="sum",
            aggregation_column="Amount",
            group_by="Customer",
        )
        result = evaluate_question(FakePage(), "revenue by customer", ctx, intent=intent)
        assert result.intent == QuestionIntent.AGGREGATE
        assert isinstance(result.answer, list)

    def test_aggregate_table_fallback_empty(self) -> None:
        ctx = _ctx()
        intent = IntentConfig(
            intent=QuestionIntent.AGGREGATE,
            column="Status",
            value="Nonexistent",
            aggregation="sum",
            aggregation_column="Amount",
            group_by="Customer",
        )
        result = evaluate_question(FakePage(), "revenue", ctx, intent=intent)
        assert result.not_found is True

    def test_aggregate_network_rows_no_sales_json(self) -> None:
        from fakes import FakeCapture

        ctx = _ctx()
        intent = IntentConfig(
            intent=QuestionIntent.AGGREGATE,
            aggregation="sum",
            aggregation_column="amount",
            group_by="customer",
        )
        capture = FakeCapture(["http://x/other.json"], bodies={})
        result = evaluate_question(FakePage(), "revenue", ctx, intent=intent, capture=capture)
        # Falls back to table snapshot, produces results
        assert result.intent == QuestionIntent.AGGREGATE
        assert isinstance(result.answer, list)

    def test_aggregate_network_rows_fallback_to_table(self) -> None:
        from fakes import FakeCapture

        ctx = _ctx()
        intent = IntentConfig(
            intent=QuestionIntent.AGGREGATE,
            aggregation="sum",
            aggregation_column="Amount",
            group_by="Customer",
        )
        capture = FakeCapture(["http://x/other.json"], bodies={})
        result = evaluate_question(FakePage(), "revenue", ctx, intent=intent, capture=capture)
        assert result.intent == QuestionIntent.AGGREGATE
        assert isinstance(result.answer, list)

    def test_aggregate_network_rows_not_list(self) -> None:
        from fakes import FakeCapture

        ctx = _ctx()
        intent = IntentConfig(
            intent=QuestionIntent.AGGREGATE,
            aggregation="sum",
            aggregation_column="amount",
            group_by="customer",
        )
        capture = FakeCapture(["http://x/sales.json"], bodies={"sales.json": {"not": "a list"}})
        result = evaluate_question(FakePage(), "revenue", ctx, intent=intent, capture=capture)
        # Falls back to table snapshot
        assert result.intent == QuestionIntent.AGGREGATE
        assert isinstance(result.answer, list)

    def test_count_where_no_match_returns_not_found(self) -> None:
        ctx = _ctx()
        intent = IntentConfig(intent=QuestionIntent.COUNT_WHERE, column="Status", value="Rejected")
        result = evaluate_question(FakePage(), "count rejected", ctx, intent=intent)
        assert result.not_found is True
        assert result.answer is None
        assert result.evidence.matched_rows == 0

    def test_unsupported_no_column_value(self) -> None:
        ctx = _ctx()
        intent = IntentConfig(intent=QuestionIntent.COUNT_WHERE)
        result = evaluate_question(FakePage(), "count", ctx, intent=intent)
        assert result.unsupported is True


class TestAggregateAvg:
    def test_aggregate_avg_no_group(self) -> None:
        ctx = _ctx()
        intent = IntentConfig(
            intent=QuestionIntent.AGGREGATE,
            aggregation="avg",
            aggregation_column="amount",
        )
        capture = FakeCapture(
            ["http://x/sales.json"],
            bodies={
                "sales.json": [
                    {"customer": "Acme Corp", "amountEur": 1000},
                    {"customer": "GlobalTech", "amountEur": 2000},
                ]
            },
        )
        result = evaluate_question(FakePage(), "average order value", ctx, intent=intent, capture=capture)
        assert result.intent == QuestionIntent.AGGREGATE
        assert isinstance(result.answer, list)
        assert len(result.answer) == 1
        assert "average" in result.answer[0]
        assert result.answer[0]["average"] == 1500.0

    def test_aggregate_avg_by_group(self) -> None:
        ctx = _ctx()
        intent = IntentConfig(
            intent=QuestionIntent.AGGREGATE,
            aggregation="avg",
            aggregation_column="amount",
            group_by="customer",
            limit=2,
        )
        capture = FakeCapture(
            ["http://x/sales.json"],
            bodies={
                "sales.json": [
                    {"customer": "Acme Corp", "amountEur": 1000},
                    {"customer": "Acme Corp", "amountEur": 2000},
                    {"customer": "GlobalTech", "amountEur": 3000},
                ]
            },
        )
        result = evaluate_question(FakePage(), "average amount by customer", ctx, intent=intent, capture=capture)
        assert result.intent == QuestionIntent.AGGREGATE
        assert isinstance(result.answer, list)
        assert len(result.answer) == 2
        assert "average" in result.answer[0]
        assert result.answer[0]["customer"] == "GlobalTech"
        assert result.answer[0]["average"] == 3000.0
        assert result.answer[1]["customer"] == "Acme Corp"
        assert result.answer[1]["average"] == 1500.0

    def test_aggregate_avg_with_network_rows_empty_after_filter(self) -> None:
        ctx = _ctx()
        intent = IntentConfig(
            intent=QuestionIntent.AGGREGATE,
            aggregation="avg",
            aggregation_column="amount",
            group_by="customer",
        )
        network_rows: list[dict] = []
        capture = FakeCapture(["http://x/sales.json"], bodies={"sales.json": network_rows})
        result = evaluate_question(FakePage(), "average revenue", ctx, intent=intent, capture=capture)
        # empty network_rows falls back to table snapshot which has 3 rows
        assert result.intent == QuestionIntent.AGGREGATE
        assert isinstance(result.answer, list)
