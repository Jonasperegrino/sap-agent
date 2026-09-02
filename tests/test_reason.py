"""Unit tests for question intent parsing (issue #647)."""

from __future__ import annotations

from sap_agent.schemas import QuestionIntent
from sap_agent.tools.reason import parse_question


class TestParseQuestion:
    def test_count_total(self) -> None:
        cfg = parse_question("how many orders are there?")
        assert cfg.intent == QuestionIntent.COUNT_TOTAL

    def test_count_where_year_comparer(self) -> None:
        cfg = parse_question("how many orders were built in 2026")
        assert cfg.intent == QuestionIntent.COUNT_WHERE
        assert cfg.column == "built"
        assert cfg.value == "2026"
        assert cfg.comparer == "year"

    def test_count_where_by_status_word(self) -> None:
        cfg = parse_question("how many orders with status Approved")
        assert cfg.intent == QuestionIntent.COUNT_WHERE
        assert cfg.column == "status"
        assert cfg.value == "Approved"
        assert cfg.comparer == "exact"

    def test_count_where_are_value(self) -> None:
        cfg = parse_question("how many orders are Approved")
        assert cfg.intent == QuestionIntent.COUNT_WHERE
        assert cfg.column == "status"
        assert cfg.value == "Approved"
        assert cfg.comparer == "exact"

    def test_count_where_is_value(self) -> None:
        cfg = parse_question("how many orders are Cancelled")
        assert cfg.intent == QuestionIntent.COUNT_WHERE
        assert cfg.column == "status"
        assert cfg.value == "Cancelled"
        assert cfg.comparer == "exact"

    def test_count_total_there_not_value(self) -> None:
        cfg = parse_question("how many orders are there?")
        assert cfg.intent == QuestionIntent.COUNT_TOTAL

    def test_existence(self) -> None:
        cfg = parse_question("is there any approved order?")
        assert cfg.intent == QuestionIntent.EXISTENCE
        assert cfg.column == "status"
        assert cfg.value == "approved"

    def test_existence_without_value_asks_followup(self) -> None:
        cfg = parse_question("is there any order?")
        assert cfg.intent == QuestionIntent.EXISTENCE
        assert cfg.follow_up != ""

    def test_lookup(self) -> None:
        cfg = parse_question("find the order for Acme Corp")
        assert cfg.intent == QuestionIntent.LOOKUP

    def test_count_products_total(self) -> None:
        cfg = parse_question("how many products are there?")
        assert cfg.intent == QuestionIntent.COUNT_TOTAL

    def test_count_products_by_category(self) -> None:
        cfg = parse_question("how many products with category Machinery")
        assert cfg.intent == QuestionIntent.COUNT_WHERE
        assert cfg.column == "category"
        assert cfg.value == "Machinery"

    def test_count_products_in_stock_counts_all(self) -> None:
        cfg = parse_question("how many products are in stock")
        assert cfg.intent == QuestionIntent.COUNT_TOTAL

    def test_count_orders_for_customer_by_company_name(self) -> None:
        cfg = parse_question("how many orders for Acme Corp")
        assert cfg.intent == QuestionIntent.COUNT_WHERE
        assert cfg.column == "customer"
        assert cfg.value == "Acme Corp"
        assert cfg.comparer == "exact"

    def test_unsupported(self) -> None:
        cfg = parse_question("play some jazz for me")
        assert cfg.intent == QuestionIntent.UNSUPPORTED
        assert cfg.follow_up != ""

    def test_empty_question(self) -> None:
        cfg = parse_question("   ")
        assert cfg.intent == QuestionIntent.UNSUPPORTED


class TestMultiWordValues:
    """Multi-word value extraction (#666): greedy prefix must stop at the LAST
    separator so values never absorb a leading preposition."""

    def test_known_multiword_customer(self) -> None:
        cfg = parse_question("how many orders are from Nordic Supply")
        assert cfg.intent == QuestionIntent.COUNT_WHERE
        assert cfg.column == "customer"
        assert cfg.value == "Nordic Supply"
        assert cfg.comparer == "exact"

    def test_customer_suffix_heuristic_energy(self) -> None:
        cfg = parse_question("how many orders are from Bluewave Energy")
        assert cfg.intent == QuestionIntent.COUNT_WHERE
        assert cfg.column == "customer"
        assert cfg.value == "Bluewave Energy"

    def test_customer_suffix_heuristic_gmbh(self) -> None:
        cfg = parse_question("how many orders are for EuroParts GmbH")
        assert cfg.intent == QuestionIntent.COUNT_WHERE
        assert cfg.column == "customer"
        assert cfg.value == "EuroParts GmbH"

    def test_value_does_not_absorb_preposition(self) -> None:
        cfg = parse_question("how many orders are from Acme Corp")
        assert cfg.intent == QuestionIntent.COUNT_WHERE
        assert cfg.column == "customer"
        assert cfg.value == "Acme Corp"
        assert not cfg.value.startswith("from")

    def test_year_value_still_wins_over_customer_heuristic(self) -> None:
        cfg = parse_question("how many orders were built in 2026")
        assert cfg.intent == QuestionIntent.COUNT_WHERE
        assert cfg.column == "built"
        assert cfg.value == "2026"
        assert cfg.comparer == "year"
