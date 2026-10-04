"""Video slice (plan.md): reference date, gate thresholds, reconcile, credit, story."""

from __future__ import annotations

from sap_agent.schemas import Config
from sap_agent.tools import decide
from sap_agent.tools.credit import credit_exposure
from sap_agent.tools.qa import classify_issue, detect_blocker, verdict_no_go
from sap_agent.tools.reconcile import reconcile
from sap_agent.tools.report import classify_failure, write_sweep_reports
from sap_agent.tools.story import STORY_DATE, run_story


def test_reference_date_pin_and_default() -> None:
    assert Config(reference_date="2026-10-05").resolve_last_month() == ("2026-09-01", "2026-09-30")
    assert Config(reference_date="").effective_reference_date()  # system date, non-empty


def test_gate_thresholds() -> None:
    assert decide.INTENT_THRESHOLD == 0.80
    assert decide.SEVERITY_THRESHOLD == 0.75 and decide.VERDICT_THRESHOLD == 0.75
    # allow-list drops secrets
    assert decide.allow_listed("intent", {"question": "q", "password": "x"}) == {"question": "q"}


def test_reconcile_plan_numbers() -> None:
    from sap_agent.tools.story import HISTORY_ROWS, RECONCILE_ROWS

    rec = reconcile(179956.50, RECONCILE_ROWS, HISTORY_ROWS)
    assert (rec["revenue"], rec["delta"], rec["explained_total"]) == (162456.50, 17500.00, 17500.00)
    assert (rec["dashboard_only"], rec["gap_pending"], rec["gap_pending_total"]) == (10, 4, 34730.50)


def test_blocker_gate_and_sweep() -> None:
    assert classify_issue("toast_or_empty").value == "blocker"
    assert detect_blocker(toast_visible=False, row_count=5) is False
    assert detect_blocker(toast_visible=True, row_count=5) is True
    assert classify_failure("toast_or_empty").value == "product_bug"


def test_story_regenerates_plan_numbers(tmp_path) -> None:
    out = run_story(tmp_path / "story")
    assert out == {"uc1": 7241.0, "uc2": "no-go", "uc3": 17500.0}
    assert STORY_DATE == "2026-10-05"
    # rerun byte-identical
    first = sorted((tmp_path / "story").rglob("*"))
    sums1 = [p.read_bytes() for p in first if p.is_file()]
    run_story(tmp_path / "story")
    sums2 = [p.read_bytes() for p in sorted((tmp_path / "story").rglob("*")) if p.is_file()]
    assert sums1 == sums2


def test_credit_two_step_never_zero(tmp_path) -> None:
    from sap_agent.tools.story import CREDIT_CUSTOMERS, CREDIT_ORDERS

    got = credit_exposure(CREDIT_ORDERS, CREDIT_CUSTOMERS, per_customer="B")
    assert got["total"] == 7241.00 and got["checksum"]
    missed = credit_exposure(CREDIT_ORDERS, CREDIT_CUSTOMERS, period=("1999-01-01", "1999-01-31"))
    assert missed["not_found"] is True and missed["answer"] is None and "latest period" in missed["message"]
    assert write_sweep_reports is not None and verdict_no_go is not None
