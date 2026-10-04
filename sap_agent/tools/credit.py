"""Credit exposure two-step only (WP5).

Intent: period, status open=Pending+Approved disclosed, rating filter, per-customer.
Planner: Dashboard orders only, ratings via Customer Detail once per customer.
# ponytail: two-step only. Full join if N-customers slow.
"""

from __future__ import annotations

import hashlib
import json

from .reconcile import _amt

OPEN_STATUSES = ("Pending", "Approved")
OPEN_DISCLOSURE = "open = Pending + Approved"


def credit_exposure(
    orders: list[dict],
    customers: list[dict],
    *,
    period: tuple[str, str] | None = None,
    min_rating: str | None = None,
    per_customer: str | None = None,
    latest_period: str = "2026-09",
) -> dict:
    """Two-step: filter dashboard orders, join one rating lookup per customer.

    `not_found` offers latest period, never 0.
    """
    by_name = {str(c.get("name", "")).lower(): str(c.get("creditRating", "")) for c in customers}
    if period is not None:
        start, end = period
        scoped = [r for r in orders if start <= str(r.get("built", ""))[:10] <= end]
    else:
        scoped = list(orders)
    if not scoped:
        return {
            "not_found": True,
            "message": f"no rows in period; latest period with data: {latest_period}",
            "latest_period": latest_period,
            "answer": None,
        }
    if per_customer:
        scoped = [r for r in scoped if str(r.get("customer", "")).lower() == per_customer.lower()]
    if min_rating:
        scoped = [r for r in scoped if by_name.get(str(r.get("customer", "")).lower(), "") == min_rating]
    open_rows = [r for r in scoped if str(r.get("status", "")) in OPEN_STATUSES]
    if not open_rows:
        return {
            "not_found": True,
            "message": f"no open rows ({OPEN_DISCLOSURE}); latest period: {latest_period}",
            "latest_period": latest_period,
            "answer": None,
        }
    breakdown = [
        {
            "id": str(r.get("id", "")),
            "customer": str(r.get("customer", "")),
            "amount": round(_amt(r), 2),
            "rating": by_name.get(str(r.get("customer", "")).lower(), ""),
        }
        for r in open_rows
    ]
    total = round(sum(float(b["amount"]) for b in breakdown), 2)
    checksum = hashlib.sha256(json.dumps(breakdown, sort_keys=True).encode()).hexdigest()[:16]
    return {
        "not_found": False,
        "total": total,
        "breakdown": breakdown,
        "checksum": checksum,
        "open_definition": OPEN_DISCLOSURE,
        "count": len(breakdown),
    }
