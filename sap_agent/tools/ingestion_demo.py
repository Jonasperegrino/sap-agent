"""Deterministic demo case for a non-EU currency mapping defect.

This is a simulated backend diagnostic, not a live SAP ingestion service.
Keep the sample values aligned with the SO-1024 record in sap-fiori/data/sales.json
and with the dashboard totals that record produces (Total Value vs. sum of amounts).
"""

from __future__ import annotations

from typing import TypedDict

QUESTION = "Why does the dashboard Total Value differ from the sum of the order amounts?"


class DemoCase(TypedDict):
    customer: str
    customer_id: str
    order_id: str
    source_amount: float
    source_currency: str
    fx_rate_to_eur: float
    display_amount_eur: float
    stored_amount_eur: float
    ingestion_path: str
    status: str
    order_date: str
    dashboard_total_eur: float
    order_rows_sum_eur: float
    demo_only: bool


class Finding(TypedDict):
    finding: str
    severity: str
    expected: str
    actual: str


class BackendFinding(Finding):
    runner: str
    ingestion_path: str


class DemoResult(TypedDict):
    question: str
    case: DemoCase
    expected_amount_eur: float
    discrepancy_eur: float
    overstatement_pct: float
    frontend: Finding
    backend: BackendFinding
    decision: str


CASE: DemoCase = {
    "customer": "Aster BioMed Brazil",
    "customer_id": "C-1011",
    "order_id": "SO-1024",
    "source_amount": 25000.0,
    "source_currency": "BRL",
    "fx_rate_to_eur": 0.16,
    "display_amount_eur": 4000.0,
    "stored_amount_eur": 25000.0,
    "ingestion_path": "non_eu_v2",
    "status": "Approved",
    "order_date": "2026-10-02",
    # Seeded dashboard snapshot: Total Value KPI vs. the sum of the Amount column.
    "dashboard_total_eur": 194956.5,
    "order_rows_sum_eur": 173956.5,
    "demo_only": True,
}


def matches_demo_question(question: str) -> bool:
    """Match the scripted SO-1024 prompt, including pasted Markdown quotes."""

    def normalize(value: str) -> str:
        lines = [line.strip().removeprefix(">").strip() for line in value.splitlines()]
        return " ".join(" ".join(lines).casefold().split()).strip(" \t\r\n>\"'“”‘’?.!")

    normalized = normalize(question)
    base_question = normalize(QUESTION)
    return normalized == base_question or normalized.startswith(base_question + " ")


def analyze_demo_ingestion() -> DemoResult:
    """Return frontend and simulated backend evidence for the seeded demo case."""
    expected = round(CASE["source_amount"] * CASE["fx_rate_to_eur"], 2)
    actual = float(CASE["stored_amount_eur"])
    discrepancy = round(actual - expected, 2)
    # Share of the true total (sum of the order rows) by which the KPI is overstated.
    overstatement_pct = round(discrepancy / CASE["order_rows_sum_eur"] * 100, 1)
    return {
        "question": QUESTION,
        "case": CASE.copy(),
        "expected_amount_eur": expected,
        "discrepancy_eur": discrepancy,
        "overstatement_pct": overstatement_pct,
        "frontend": {
            "finding": "dashboard_kpi_mismatch",
            "severity": "high",
            "expected": f"Dashboard contribution for {CASE['order_id']} is €{expected:,.2f}",
            "actual": (
                f"Order row displays €{CASE['display_amount_eur']:,.2f}; KPI source field contributes €{actual:,.2f}"
            ),
        },
        "backend": {
            "finding": "currency_normalization_failed",
            "severity": "high",
            "runner": "deterministic demo simulation",
            "expected": (
                f"source_amount × fx_rate_to_eur = {CASE['source_currency']} "
                f"{CASE['source_amount']:,.2f} × {CASE['fx_rate_to_eur']} = €{expected:,.2f}"
            ),
            "actual": (
                f"amountEur persisted as €{actual:,.2f} (raw {CASE['source_currency']} amount copied "
                "without conversion)"
            ),
            "ingestion_path": CASE["ingestion_path"],
        },
        "decision": (
            "Hold revenue report; fix non-EU currency normalization, reprocess SO-1024, and rerun reconciliation."
        ),
    }


def report_markdown(result: DemoResult | None = None) -> str:
    """Render a downloadable combined finding with the simulation clearly labeled."""
    result = result or analyze_demo_ingestion()
    case = result["case"]
    frontend = result["frontend"]
    backend = result["backend"]
    return (
        "# Demo bug report — SO-1024 currency mapping\n\n"
        "> Deterministic demo scenario. The backend runner is simulated; no live ingestion service was called.\n\n"
        "**Classification:** `product_bug` · **Severity:** high · **Decision:** hold revenue report\n\n"
        "## Frontend finding — SAP Fiori\n\n"
        f"{frontend['actual']}\n\n"
        "## Backend finding — simulated ingestion diagnostic\n\n"
        f"{backend['expected']}\n\n"
        f"{backend['actual']}\n\n"
        f"Ingestion path: `{case['ingestion_path']}`. Order: `{case['order_id']}` for {case['customer']} "
        f"(`{case['customer_id']}`).\n\n"
        f"**KPI overstatement:** €{result['discrepancy_eur']:,.2f} — dashboard Total Value "
        f"€{case['dashboard_total_eur']:,.2f} vs. €{case['order_rows_sum_eur']:,.2f} summed from the order rows "
        f"({result['overstatement_pct']:.1f}% too high).\n\n"
        "## Reproduction\n\n"
        "1. Open the Sales Dashboard and compare the order row with Total Value.\n"
        f"2. Inspect `{case['order_id']}` source currency and the pinned demo FX rate.\n"
        f"3. Recompute `{case['source_currency']} {case['source_amount']:,.2f} × {case['fx_rate_to_eur']}` "
        "and compare it with `amountEur`.\n"
        "4. Observe that the non-EU mapping copied the unconverted source amount into the EUR field.\n\n"
        f"## Recommended action\n\n{result['decision']}\n"
    )
