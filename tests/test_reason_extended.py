"""Extended unit tests for reason tool — parse_question_with_llm + more branches."""

from __future__ import annotations

from unittest.mock import patch

from pydantic import SecretStr

from sap_agent.schemas import Config, IntentConfig, QuestionIntent
from sap_agent.tools.reason import parse_question, parse_question_with_llm


class TestParseQuestionWithLlm:
    def test_rule_first_returns_without_llm(self) -> None:
        # "how many orders" matches COUNT_TOTAL — no LLM call needed
        result = parse_question_with_llm("how many orders are there?")
        assert result.intent == QuestionIntent.COUNT_TOTAL

    def test_no_config_returns_rule_result(self) -> None:
        result = parse_question_with_llm("play jazz", config=None)
        assert result.intent == QuestionIntent.UNSUPPORTED

    def test_config_without_llm_returns_rule_result(self) -> None:
        cfg = Config(app_url="http://x", username="u", password="p")
        result = parse_question_with_llm("play jazz", config=cfg)
        assert result.intent == QuestionIntent.UNSUPPORTED

    @patch("sap_agent.tools.llm.call_llm_for_intent")
    def test_llm_fallback_when_rule_unsupported(self, mock_llm) -> None:
        cfg = Config(app_url="http://x", username="u", password="p", llm_api_key=SecretStr("key"))
        mock_llm.return_value = IntentConfig(intent=QuestionIntent.COUNT_WHERE, column="status", value="Approved")
        result = parse_question_with_llm("play jazz", config=cfg)
        assert result.intent == QuestionIntent.COUNT_WHERE
        assert result.column == "status"
        mock_llm.assert_called_once()

    @patch("sap_agent.tools.llm.call_llm_for_intent")
    def test_llm_returns_unsupported_falls_back(self, mock_llm) -> None:
        cfg = Config(app_url="http://x", username="u", password="p", llm_api_key=SecretStr("key"))
        mock_llm.return_value = IntentConfig(intent=QuestionIntent.UNSUPPORTED)
        result = parse_question_with_llm("play jazz", config=cfg)
        assert result.intent == QuestionIntent.UNSUPPORTED

    @patch("sap_agent.tools.llm.call_llm_for_intent")
    def test_llm_returns_none_falls_back(self, mock_llm) -> None:
        cfg = Config(app_url="http://x", username="u", password="p", llm_api_key=SecretStr("key"))
        mock_llm.return_value = None
        result = parse_question_with_llm("play jazz", config=cfg)
        assert result.intent == QuestionIntent.UNSUPPORTED

    @patch("sap_agent.tools.llm.call_llm_for_intent")
    def test_llm_exception_falls_back(self, mock_llm) -> None:
        cfg = Config(app_url="http://x", username="u", password="p", llm_api_key=SecretStr("key"))
        mock_llm.side_effect = ConnectionError("refused")
        result = parse_question_with_llm("play jazz", config=cfg)
        assert result.intent == QuestionIntent.UNSUPPORTED


class TestParseQuestionExtendedBranches:
    def test_count_total_no_match_in_value(self) -> None:
        # "how many orders" with no WHERE value — line 158
        result = parse_question("count orders")
        assert result.intent == QuestionIntent.COUNT_TOTAL

    def test_existence_no_column_keyword(self) -> None:
        # existence pattern matches but no column keyword → follow_up
        result = parse_question("does any exist?")
        assert result.intent == QuestionIntent.EXISTENCE
        assert result.follow_up != ""

    def test_count_where_customer_suffix(self) -> None:
        # _looks_like_customer triggers on suffix
        result = parse_question("how many orders for Acme Corp")
        assert result.intent == QuestionIntent.COUNT_WHERE
        assert result.column == "customer"

    def test_count_where_value_starts_with_column(self) -> None:
        # value.lower().startswith(column.lower()) — line 148-149
        result = parse_question("how many orders with status Approved")
        assert result.intent == QuestionIntent.COUNT_WHERE
        assert result.column == "status"
        assert result.value == "Approved"

    def test_count_total_there_value(self) -> None:
        # value == "there" → COUNT_TOTAL — line 141-142
        result = parse_question("how many orders are there?")
        assert result.intent == QuestionIntent.COUNT_TOTAL


class TestAggregateExtended:
    def test_revenue_by_status(self) -> None:
        result = parse_question("revenue by status")
        assert result.intent == QuestionIntent.AGGREGATE
        assert result.aggregation == "sum"
        assert result.group_by == "status"
        assert result.aggregation_column == "amount"

    def test_orders_by_status(self) -> None:
        result = parse_question("orders by status")
        assert result.intent == QuestionIntent.AGGREGATE
        assert result.aggregation == "count"
        assert result.group_by == "status"

    def test_average_order_value(self) -> None:
        result = parse_question("average order value")
        assert result.intent == QuestionIntent.AGGREGATE
        assert result.aggregation == "avg"
        assert result.aggregation_column == "amount"
        assert result.group_by is None

    def test_average_amount_by_customer(self) -> None:
        result = parse_question("average amount by customer")
        assert result.intent == QuestionIntent.AGGREGATE
        assert result.aggregation == "avg"
        assert result.aggregation_column == "amount"
        assert result.group_by == "customer"

    def test_average_price_by_category(self) -> None:
        result = parse_question("average price by category")
        assert result.intent == QuestionIntent.AGGREGATE
        assert result.aggregation == "avg"
        assert result.aggregation_column == "price"
        assert result.group_by == "category"

    def test_how_many_products_by_category(self) -> None:
        result = parse_question("how many products by category")
        assert result.intent == QuestionIntent.AGGREGATE
        assert result.aggregation == "count"
        assert result.group_by == "category"

    def test_how_many_customers_by_country(self) -> None:
        # "how many customers by <group>" falls through to COUNT_TOTAL (routes to customers page)
        result = parse_question("how many customers by country")
        assert result.intent == QuestionIntent.COUNT_WHERE
        assert result.column == "country"

    def test_count_where_preserved(self) -> None:
        result = parse_question("how many orders are Approved")
        assert result.intent == QuestionIntent.COUNT_WHERE
        assert result.column == "status"

    def test_orders_by_customer_preserved(self) -> None:
        result = parse_question("how many orders by customer")
        assert result.intent == QuestionIntent.AGGREGATE
        assert result.aggregation == "count"
        assert result.group_by == "customer"

    def test_count_where_by_category_preserved(self) -> None:
        result = parse_question("how many products with category Machinery")
        assert result.intent == QuestionIntent.COUNT_WHERE
        assert result.column == "category"
