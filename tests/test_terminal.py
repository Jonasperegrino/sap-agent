"""Unit tests for terminal formatting helpers (#697)."""

from __future__ import annotations

from sap_agent.schemas import AccessibilityIssue, QaPageReport, QaReport, Severity, UxIssue
from sap_agent.ui import terminal


def _report() -> QaReport:
    return QaReport(
        app_url="http://x",
        pages=[
            QaPageReport(
                route="catalog",
                accessibility_issues=[AccessibilityIssue(type="missing_alt", element="<img>", severity=Severity.HIGH)],
                ux_issues=[UxIssue(type="touch_target", severity=Severity.LOW)],
                performance_hints=["1 resource(s) over 500KB"],
            )
        ],
    )


def test_print_header_renders_banner(capsys) -> None:
    terminal.set_color_enabled(False)
    terminal.print_header("SAP Fiori QA Agent")
    out = capsys.readouterr().out
    assert "SAP Fiori QA Agent" in out
    assert "═" in out


def test_print_step_renders_counter(capsys) -> None:
    terminal.set_color_enabled(False)
    terminal.print_step(2, 5, "auditing catalog")
    assert "step 2/5" in capsys.readouterr().out


def test_print_issue_renders_tag_and_type(capsys) -> None:
    terminal.set_color_enabled(False)
    terminal.print_issue("a11y", "missing_alt", Severity.HIGH, "<img>")
    out = capsys.readouterr().out
    assert "[A11Y]" in out
    assert "missing_alt" in out
    assert "HIGH" in out


def test_print_summary_renders_counts_and_boxes(capsys) -> None:
    terminal.set_color_enabled(False)
    terminal.print_summary(_report())
    out = capsys.readouterr().out
    assert "Found 2 issues: 1 high, 0 medium, 1 low" in out
    assert "┌" in out and "└" in out
    assert "accessibility" in out
    assert "UX" in out
    assert "performance" in out


def test_colors_emit_ansi_when_enabled(capsys) -> None:
    terminal.set_color_enabled(True)
    terminal.print_issue("ux", "contrast", Severity.HIGH, "<div>")
    out = capsys.readouterr().out
    assert "\033[31m" in out  # red for high
    terminal.set_color_enabled(False)


def test_print_report_path(capsys) -> None:
    terminal.set_color_enabled(False)
    terminal.print_report_path("artifacts/qa_report.json")
    assert "artifacts/qa_report.json" in capsys.readouterr().out
