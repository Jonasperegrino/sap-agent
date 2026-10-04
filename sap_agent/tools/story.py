"""Story runner (WP7): one cmd regenerates uc1,uc2,uc3 deterministic.

Resets storage, empties artifacts/story/uc1,uc2,uc3,uc4, pins date 2026-10-05,
writes answers + screenshots + decision log. Deterministic sans
timestamps/latencies. Demo mode is rules-only (no JEV/LLM calls).
# ponytail: static CSS highlight. Programmatic outline if video needs zoom.
"""

from __future__ import annotations

import json
import shutil
from contextlib import suppress
from pathlib import Path

STORY_DATE = "2026-10-05"


# Minimal 1x1 PNG, deterministic bytes.
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xe0\x96\xb3\x01\x00\x00\x9d\x00f\xd9%\x98.\x00\x00\x00\x00IEND\xaeB`\x82"
)

# WP1 seed tail: three Sept 2026 orders.
SEED_ORDERS = [
    {"id": "SO-1021", "customer": "GlobalTech", "amountEur": 4200.00, "status": "Shipped", "built": "2026-09-05"},
    {"id": "SO-1022", "customer": "Acme Corp", "amountEur": 3300.00, "status": "Approved", "built": "2026-09-12"},
    {
        "id": "SO-1023",
        "customer": "Helios Manufacturing",
        "amountEur": 2500.00,
        "status": "Shipped",
        "built": "2026-09-21",
    },
]

# WP3 fixtures: explainers + gap reproduce plan numbers exactly.
RECONCILE_ROWS = [
    {"id": "SO-1006", "customer": "X", "amountEur": 15760.00, "status": "Rejected", "built": "2026-03-01"},
    {"id": "SO-1011", "customer": "Y", "amountEur": 1740.00, "status": "Cancelled", "built": "2026-05-01"},
    {"id": "SO-1014", "customer": "A", "amountEur": 9000.00, "status": "Pending", "built": "2025-02-01"},
    {"id": "SO-1015", "customer": "B", "amountEur": 2310.50, "status": "Pending", "built": "2025-03-01"},
    {"id": "SO-1018", "customer": "C", "amountEur": 12000.00, "status": "Pending", "built": "2025-04-01"},
    {"id": "SO-1020", "customer": "D", "amountEur": 11420.00, "status": "Pending", "built": "2025-05-01"},
    {"id": "SO-R1", "customer": "R", "amountEur": 111726.00, "status": "Approved", "built": "2026-06-01"},
    {"id": "SO-R2", "customer": "R", "amountEur": 1000.00, "status": "Approved", "built": "2026-06-02"},
    {"id": "SO-R3", "customer": "R", "amountEur": 1000.00, "status": "Approved", "built": "2026-06-03"},
    {"id": "SO-R4", "customer": "R", "amountEur": 1000.00, "status": "Approved", "built": "2026-06-04"},
    {"id": "SO-R5", "customer": "R", "amountEur": 1000.00, "status": "Approved", "built": "2026-06-05"},
    {"id": "SO-R6", "customer": "R", "amountEur": 1000.00, "status": "Approved", "built": "2026-06-06"},
    {"id": "SO-R7", "customer": "R", "amountEur": 1000.00, "status": "Approved", "built": "2026-06-07"},
    *SEED_ORDERS,
]
# History page misses 10 rows: 4 Pending 2025 (€34,730.50) + 6 2026 Approved R2-R7.
HISTORY_ROWS = [
    r
    for r in RECONCILE_ROWS
    if r["id"] not in ("SO-1014", "SO-1015", "SO-1018", "SO-1020", "SO-R2", "SO-R3", "SO-R4", "SO-R5", "SO-R6", "SO-R7")
]

CREDIT_ORDERS = [
    {"id": "SO-1015", "customer": "B", "amountEur": 2310.50, "status": "Pending", "built": "2025-03-01"},
    {"id": "SO-1007", "customer": "B", "amountEur": 4930.50, "status": "Approved", "built": "2026-04-10"},
    {"id": "SO-1021", "customer": "GlobalTech", "amountEur": 4200.00, "status": "Shipped", "built": "2026-09-05"},
    {"id": "SO-1022", "customer": "Acme Corp", "amountEur": 3300.00, "status": "Approved", "built": "2026-09-12"},
    {
        "id": "SO-1023",
        "customer": "Helios Manufacturing",
        "amountEur": 2500.00,
        "status": "Shipped",
        "built": "2026-09-21",
    },
]
CREDIT_CUSTOMERS = [
    {"name": "B", "creditRating": "A"},
    {"name": "GlobalTech", "creditRating": "B"},
    {"name": "Acme Corp", "creditRating": "A"},
    {"name": "Helios Manufacturing", "creditRating": "C"},
]


def _write_png(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > 1024:
            return  # keep a real capture; stubs never overwrite it
    except OSError:
        pass
    path.write_bytes(_PNG)


def run_story(base: str | Path = "artifacts/story") -> dict:
    """Regenerate all story artifacts. Returns per-UC summary."""
    from .credit import credit_exposure
    from .decide import decide_verdict
    from .reconcile import heads_up, reconcile

    root = Path(base)
    # Preserve real captures across regen (stubs are <1KB, real shots survive).
    keep: dict[str, bytes] = {}
    if root.exists():
        for p in root.rglob("*.png"):
            try:
                if p.is_file() and p.stat().st_size > 1024:
                    keep[str(p.relative_to(root))] = p.read_bytes()
            except OSError:
                pass
        shutil.rmtree(root)
    ucs = {u: root / u for u in ("uc1", "uc2", "uc3", "uc4")}
    for d in ucs.values():
        d.mkdir(parents=True, exist_ok=True)

    decisions: list[dict] = []

    def log(kind: str, provider: str, value: str, outcome: str) -> None:
        decisions.append({"kind": kind, "provider": provider, "value": value, "outcome": outcome})

    # UC1 credit two-step (WP5)
    # UC1 credit two-step (WP5): per-customer B -> SO-1015 + SO-1007 = €7,241.00;
    # Sept 2026 revenue = seed €10,000.00
    c = credit_exposure(CREDIT_ORDERS, CREDIT_CUSTOMERS, per_customer="B")
    sept = [r for r in CREDIT_ORDERS if str(r.get("built", ""))[:7] == "2026-09"]
    sept_rev = round(
        sum(float(r["amountEur"]) for r in sept if str(r["status"]).lower() in ("pending", "approved", "shipped")), 2
    )
    log("intent", "rules", "aggregate:credit-exposure", "accepted")
    (ucs["uc1"] / "answer.json").write_text(
        json.dumps(
            {
                "uc": "uc1",
                "reference_date": STORY_DATE,
                "total": c["total"],
                "sept_2026_revenue": sept_rev,
                "breakdown": c["breakdown"],
                "checksum": c["checksum"],
                "open_definition": c["open_definition"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    _write_png(ucs["uc1"] / "dashboard.png")
    _write_png(ucs["uc1"] / "customer-detail.png")

    # UC2 sweep (WP4): toast OR zero-rows -> one blocker; verdict no-go
    from ..schemas import AccessibilityIssue, QaPageReport, QaReport, Severity
    from .qa import detect_blocker, verdict_no_go
    from .report import classify_failure, write_sweep_reports

    assert detect_blocker(toast_visible=True, row_count=0) is True
    page = QaPageReport(
        route="dashboard",
        accessibility_issues=[
            AccessibilityIssue(
                type="toast_or_empty",
                element="toast",
                severity=Severity.BLOCKER,
                suggestion="data failed to load — fix data path, keep KPI visible or show error",
            )
        ],
    )
    rep = QaReport(app_url="story-fixture", generated_at="", pages=[page])
    verdict = "no-go" if verdict_no_go(rep) else "go"
    log("severity", "rules", "toast_or_empty=blocker", "accepted")
    log("verdict", "rules", verdict, "accepted")
    summary, per_issue = write_sweep_reports(rep, ucs["uc2"], verdict=verdict)
    (ucs["uc2"] / "answer.json").write_text(
        json.dumps(
            {
                "uc": "uc2",
                "reference_date": STORY_DATE,
                "verdict": verdict,
                "classification": classify_failure("toast_or_empty").value,
                "summary": summary.name,
                "issues": sorted(p.name for p in per_issue),
            },
            indent=2,
            sort_keys=True,
        )
    )
    _write_png(ucs["uc2"] / "regression.png")

    # UC3 reconcile hero (WP3)
    rec = reconcile(179956.50, RECONCILE_ROWS, HISTORY_ROWS, scope_label="2026 orders")
    assert rec["revenue"] == 162456.50, rec
    assert rec["delta"] == 17500.00, rec
    assert rec["explained_total"] == 17500.00, rec
    assert rec["dashboard_only"] == 10, rec
    assert rec["gap_pending"] == 4 and rec["gap_pending_total"] == 34730.50, rec
    verdict3, sev3, _ = decide_verdict(rec["delta"], explained=True, pending_older_than_scope=False, closed_only=False)
    log("verdict", "rules", verdict3, "accepted")
    (ucs["uc3"] / "answer.json").write_text(
        json.dumps(
            {
                "uc": "uc3",
                "reference_date": STORY_DATE,
                **rec,
                "heads_up": heads_up(rec),
                "verdict": verdict3,
                "severity": sev3,
            },
            indent=2,
            sort_keys=True,
        )
    )
    _write_png(ucs["uc3"] / "kpi.png")

    # UC4: reconcile the Fiori-visible value with a simulated backend import check.
    from .ingestion_demo import analyze_demo_ingestion, report_markdown

    ingestion = analyze_demo_ingestion()
    log("backend_ingestion", "deterministic-simulation", "currency_normalization_failed", "reported-demo-only")
    (ucs["uc4"] / "answer.json").write_text(json.dumps(ingestion, indent=2, sort_keys=True))
    (ucs["uc4"] / "bug_report.md").write_text(report_markdown(ingestion))

    # Shared deterministic decision log (no timestamps/latencies) + recorded JEV example
    log("intent", "jev-recorded", "count_where:built=2026", "accepted-example")
    for d in ucs.values():
        (d / "decisions.jsonl").write_text("\n".join(json.dumps(e, sort_keys=True) for e in decisions) + "\n")
    for rel, data in keep.items():
        with suppress(OSError):
            (root / rel).write_bytes(data)
    return {"uc1": c["total"], "uc2": verdict, "uc3": rec["delta"]}
