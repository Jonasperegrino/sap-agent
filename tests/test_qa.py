"""Unit tests for the QA workflow (#685): schemas, consistency, report IO."""

from __future__ import annotations

import json

from sap_agent.context import SessionContext
from sap_agent.schemas import (
    AccessibilityIssue,
    Config,
    QaPageReport,
    QaReport,
    ScreenshotResult,
    Severity,
    UxIssue,
)
from sap_agent.tools.qa import _consistency_check, write_qa_report


def _report(*severities: Severity) -> QaReport:
    issues = [AccessibilityIssue(type="x", element="<el>", severity=s) for s in severities]
    pages = [QaPageReport(route=f"r{i}", accessibility_issues=[issue]) for i, issue in enumerate(issues)]
    return QaReport(app_url="http://x", pages=pages)


def test_total_issues_counts_all_pages() -> None:
    report = _report(Severity.HIGH, Severity.MEDIUM, Severity.LOW)
    assert report.total_issues == 3


def test_counts_by_severity() -> None:
    report = _report(Severity.HIGH, Severity.LOW)
    assert report.counts_by_severity() == {"high": 1, "medium": 0, "low": 1}


def test_counts_include_ux_issues() -> None:
    report = QaReport(
        app_url="http://x",
        pages=[
            QaPageReport(
                route="r",
                ux_issues=[UxIssue(type="overlap", severity=Severity.MEDIUM)],
            )
        ],
    )
    assert report.total_issues == 1
    assert report.counts_by_severity()["medium"] == 1


def test_consistency_check_flags_divergent_title() -> None:
    issues = _consistency_check({"dashboard": 16.0, "catalog": 16.0, "orders": 11.0})
    assert "orders" in issues
    assert issues["orders"][0].type == "page_consistency"
    assert issues["orders"][0].severity is Severity.MEDIUM
    assert "dashboard" not in issues


def test_consistency_check_skips_low_signal() -> None:
    assert _consistency_check({"dashboard": 16.0}) == {}
    assert _consistency_check({"dashboard": 0.0, "catalog": 0.0}) == {}


def test_write_qa_report_roundtrip(tmp_path) -> None:
    config = Config(app_url="http://x", artifacts_dir=str(tmp_path))
    ctx = SessionContext(config)
    report = _report(Severity.LOW)

    path = write_qa_report(report, ctx)
    written = json.loads(tmp_path.joinpath("qa_report.json").read_text())

    assert path.endswith("qa_report.json")
    assert written["app_url"] == "http://x"
    assert written["pages"][0]["route"] == "r0"


def test_qa_report_to_markdown_lists_pages_and_issues() -> None:
    report = QaReport(
        app_url="http://x",
        generated_at="2026-08-21T10:00:00+00:00",
        pages=[
            QaPageReport(
                route="dashboard",
                screenshots=[ScreenshotResult(route="dashboard", path="s.png", width=1280, height=800)],
                accessibility_issues=[
                    AccessibilityIssue(
                        type="form_label", element="<input>", severity=Severity.HIGH, suggestion="add label"
                    )
                ],
                ux_issues=[UxIssue(type="touch_target", severity=Severity.MEDIUM, suggestion="enlarge")],
                performance_hints=["high resource count: 50 requests on this page"],
            )
        ],
    )

    md = report.to_markdown()

    assert "# QA Report — http://x" in md
    assert "## dashboard" in md
    assert "[a11y/high] form_label: <input> — add label" in md
    assert "[ux/medium] touch_target" in md
    assert "- [perf] high resource count" in md
    assert "**Issues**: 2" in md


def test_qa_report_to_markdown_empty_report() -> None:
    md = QaReport(app_url="http://x").to_markdown()

    assert "# QA Report — http://x" in md
    assert "**Issues**: 0" in md


def test_cli_qa_accepts_format_flag() -> None:
    from sap_agent.cli import _build_parser

    args = _build_parser().parse_args(["--app", "http://x", "qa", "--format", "markdown"])
    assert args.format == "markdown"
    assert _build_parser().parse_args(["qa"]).format is None
