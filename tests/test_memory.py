"""Unit tests for agent memory across runs (#694)."""

from __future__ import annotations

from pathlib import Path

from sap_agent.memory import AgentMemory
from sap_agent.schemas import (
    AccessibilityIssue,
    QaPageReport,
    QaReport,
    Severity,
    UxIssue,
)


def _report(
    generated_at: str,
    *,
    a11y_types: tuple[str, ...] = (),
    ux_types: tuple[str, ...] = (),
) -> QaReport:
    page = QaPageReport(
        route="dashboard",
        accessibility_issues=[AccessibilityIssue(type=t, element="<el>", severity=Severity.HIGH) for t in a11y_types],
        ux_issues=[UxIssue(type=t, element="<el>", severity=Severity.MEDIUM) for t in ux_types],
    )
    return QaReport(app_url="http://x", generated_at=generated_at, pages=[page])


def _memory(tmp_path: Path) -> AgentMemory:
    return AgentMemory(tmp_path / "history")


class TestHistoryStorage:
    def test_load_history_empty_when_no_files(self, tmp_path) -> None:
        assert _memory(tmp_path).load_history() == []

    def test_save_then_load_roundtrip(self, tmp_path) -> None:
        memory = _memory(tmp_path)
        report = _report("2026-08-21T10:00:00+00:00", a11y_types=("form_label",))

        path = memory.save_report(report)
        loaded = memory.load_history()

        assert path.exists()
        assert path.name.startswith("qa_report_")
        assert len(loaded) == 1
        assert loaded[0].generated_at == "2026-08-21T10:00:00+00:00"
        assert loaded[0].pages[0].accessibility_issues[0].type == "form_label"

    def test_unreadable_file_is_skipped(self, tmp_path) -> None:
        memory = _memory(tmp_path)
        memory.save_report(_report("2026-08-21T10:00:00+00:00"))
        (tmp_path / "history" / "qa_report_broken.json").write_text("{not json")

        loaded = memory.load_history()

        assert len(loaded) == 1


class TestDiffReports:
    def test_classifies_new_persistent_resolved(self, tmp_path) -> None:
        memory = _memory(tmp_path)
        old = _report("t1", a11y_types=("form_label", "contrast"), ux_types=("overlap",))
        new = _report("t2", a11y_types=("contrast", "focus"), ux_types=("overlap",))

        diff = memory.diff_reports(old, new)

        by_status = {(i.source, i.type): i.status for i in diff.issues}
        assert by_status[("a11y", "contrast")] == "persistent"
        assert by_status[("a11y", "form_label")] == "resolved"
        assert by_status[("ux", "overlap")] == "persistent"
        assert by_status[("a11y", "focus")] == "new"
        assert diff.counts_by_status() == {"new": 1, "persistent": 2, "resolved": 1}

    def test_same_issue_on_different_page_is_distinct(self, tmp_path) -> None:
        memory = _memory(tmp_path)

        def report(route: str, at: str) -> QaReport:
            page = QaPageReport(
                route=route,
                accessibility_issues=[AccessibilityIssue(type="form_label", element="<el>")],
            )
            return QaReport(app_url="http://x", generated_at=at, pages=[page])

        diff = memory.diff_reports(report("dashboard", "t1"), report("catalog", "t2"))

        statuses = {issue.status for issue in diff.issues}
        assert statuses == {"new", "resolved"}

    def test_duplicate_issues_deduplicate(self, tmp_path) -> None:
        memory = _memory(tmp_path)
        page = QaPageReport(
            route="dashboard",
            accessibility_issues=[
                AccessibilityIssue(type="form_label", element="<el>"),
                AccessibilityIssue(type="form_label", element="<el>"),
            ],
        )
        old = QaReport(app_url="http://x", generated_at="t1")
        new = QaReport(app_url="http://x", generated_at="t2", pages=[page])

        diff = memory.diff_reports(old, new)

        assert len(diff.issues) == 1
