"""The seeded SO-1024 currency case: numbers stay aligned with the Fiori fixture."""

from __future__ import annotations

from sap_agent.tools.ingestion_demo import (
    CASE,
    QUESTION,
    analyze_demo_ingestion,
    matches_demo_question,
    report_markdown,
)


def test_case_is_internally_consistent() -> None:
    result = analyze_demo_ingestion()
    # The order row shows the correctly converted amount; the KPI counts the raw source amount.
    assert result["expected_amount_eur"] == CASE["display_amount_eur"] == 4000.0
    assert CASE["stored_amount_eur"] == CASE["source_amount"] == 25000.0
    assert result["discrepancy_eur"] == 21000.0
    # Dashboard totals differ by exactly this one order's discrepancy.
    assert CASE["dashboard_total_eur"] - CASE["order_rows_sum_eur"] == result["discrepancy_eur"]


def test_total_value_is_overstated_by_more_than_ten_percent() -> None:
    result = analyze_demo_ingestion()
    assert result["overstatement_pct"] == 12.1
    assert result["discrepancy_eur"] / CASE["dashboard_total_eur"] > 0.10


def test_report_states_the_numbers_and_the_simulation() -> None:
    report = report_markdown()
    for expected in ("BRL 25,000.00 × 0.16", "€21,000.00", "€194,956.50", "€173,956.50", "12.1% too high"):
        assert expected in report
    assert "simulated" in report


def test_scripted_question_matches_with_quotes_and_punctuation() -> None:
    assert matches_demo_question(QUESTION)
    assert matches_demo_question(f"> “{QUESTION.rstrip('?')}”")
    assert not matches_demo_question("How many orders are pending?")
