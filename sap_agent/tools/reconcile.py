"""Reconcile (WP3 hero): metric registry + delta + coverage.

Registry: Revenue = Pending+Approved+Shipped. Gross = all. KPI maps Gross.
Expected (WP1 seed): KPI €179,956.50, revenue €162,456.50,
delta €17,500.00 = SO-1006 €15,760 + SO-1011 €1,740.
Dashboard-only 10 (2025), 4 Pending €34,730.50.
"""

from __future__ import annotations

REVENUE_STATUSES = frozenset({"pending", "approved", "shipped"})
KPI_LABEL = "Total Value"
KPI_METRIC = "Gross"


def _amt(row: dict) -> float:
    v = row.get("amountEur", row.get("amount", 0))
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace("€", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def gross(rows: list[dict]) -> float:
    return round(sum(_amt(r) for r in rows), 2)


def revenue(rows: list[dict]) -> float:
    return round(sum(_amt(r) for r in rows if str(r.get("status", "")).lower() in REVENUE_STATUSES), 2)


def reconcile(kpi: float, rows: list[dict], history_rows: list[dict], scope_label: str = "") -> dict:
    """Structured result: kpi, revenue, delta, explainers, dashboard-only gap."""
    rev = revenue(rows)
    delta = round(kpi - rev, 2)
    ids = {str(r.get("id", "")) for r in rows}
    hist_ids = {str(r.get("id", "")) for r in history_rows}
    dash_only = [r for r in rows if str(r.get("id", "")) not in hist_ids]
    explainers = [
        {"id": str(r.get("id", "")), "status": str(r.get("status", "")), "amount": round(_amt(r), 2)}
        for r in rows
        if str(r.get("status", "")).lower() in ("rejected", "cancelled")
    ]
    gap_pending = [r for r in dash_only if str(r.get("status", "")).lower() == "pending"]
    return {
        "kpi": round(kpi, 2),
        "kpi_label": KPI_LABEL,
        "kpi_metric": KPI_METRIC,
        "revenue": rev,
        "delta": delta,
        "explained_by": explainers,
        "explained_total": round(sum(float(e["amount"]) for e in explainers), 2),
        "dashboard_only": len(dash_only),
        "dashboard_only_ids": sorted(ids - hist_ids),
        "gap_pending": len(gap_pending),
        "gap_pending_total": round(sum(_amt(r) for r in gap_pending), 2),
        "scope": scope_label,
    }


def heads_up(rec: dict) -> str:
    if rec["delta"] == 0:
        return ""
    return (
        f"Heads-up: {rec['kpi_label']} €{rec['kpi']:,.2f} ({rec['kpi_metric']}) differs from "
        f"revenue €{rec['revenue']:,.2f} by €{rec['delta']:,.2f}."
    )
