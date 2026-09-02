"""Unit tests for severity classification (#698)."""

from __future__ import annotations

import pytest

from sap_agent.schemas import AccessibilityIssue, QaPageReport, QaReport, Severity, UxIssue
from sap_agent.tools.qa import _align_severities
from sap_agent.tools.severity import classify_issue


@pytest.mark.parametrize(
    ("issue_type", "expected"),
    [
        ("missing_alt", Severity.HIGH),
        ("missing_label", Severity.HIGH),
        ("form_label", Severity.MEDIUM),
        ("contrast", Severity.MEDIUM),
        ("heading_order", Severity.MEDIUM),
        ("visual_hierarchy", Severity.MEDIUM),
        ("spacing_inconsistency", Severity.MEDIUM),
        ("alignment_issue", Severity.MEDIUM),
        ("interaction_affordance", Severity.MEDIUM),
        ("page_consistency", Severity.MEDIUM),
        ("empty_alt", Severity.LOW),
        ("heading_hierarchy", Severity.LOW),
        ("touch_target", Severity.LOW),
        ("unknown_future_check", Severity.LOW),
    ],
)
def test_classify_issue(issue_type: str, expected: Severity) -> None:
    assert classify_issue(issue_type) is expected


def test_align_severities_replaces_script_severities() -> None:
    report = QaPageReport(
        route="catalog",
        accessibility_issues=[
            AccessibilityIssue(type="missing_alt", element="<img>", severity=Severity.MEDIUM),
            AccessibilityIssue(type="contrast", element="<div>", severity=Severity.LOW),
        ],
        ux_issues=[UxIssue(type="touch_target", severity=Severity.HIGH)],
    )
    _align_severities(report)
    severities = [i.severity for i in report.accessibility_issues] + [report.ux_issues[0].severity]
    assert severities == [Severity.HIGH, Severity.MEDIUM, Severity.LOW]


def test_aligned_report_aggregation() -> None:
    report = QaReport(
        app_url="http://x",
        pages=[
            QaPageReport(
                route="r",
                accessibility_issues=[
                    AccessibilityIssue(type="missing_alt", element="<img>", severity=Severity.LOW),
                    AccessibilityIssue(type="form_label", element="<input>", severity=Severity.LOW),
                ],
            )
        ],
    )
    for page in report.pages:
        _align_severities(page)
    assert report.counts_by_severity() == {"high": 1, "medium": 1, "low": 0}
