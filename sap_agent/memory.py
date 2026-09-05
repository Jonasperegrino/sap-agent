"""Agent memory across runs (#694): persist QA reports, diff consecutive runs.

Reports land in `artifacts/history/qa_report_<stamp>.json`. On a new run the
agent loads the last stored report and diffs it against the fresh one so
issues get a lifecycle (new / persistent / resolved) instead of being
re-reported blindly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .schemas import DiffIssue, DiffReport, QaReport

if TYPE_CHECKING:
    from pathlib import Path

HISTORY_DIR = "history"

IssueKey = tuple[str, str, str, str]


def _index_issues(report: QaReport) -> dict[IssueKey, dict[str, str]]:
    """Deduplicated issue identity: (route, source, type, element) -> fields."""
    index: dict[IssueKey, dict[str, str]] = {}
    for page in report.pages:
        for issue in page.accessibility_issues:
            index[(page.route, "a11y", issue.type, issue.element)] = {
                "route": page.route,
                "source": "a11y",
                "type": issue.type,
                "element": issue.element,
                "severity": issue.severity.value,
            }
        for issue in page.ux_issues:
            index[(page.route, "ux", issue.type, issue.element)] = {
                "route": page.route,
                "source": "ux",
                "type": issue.type,
                "element": issue.element,
                "severity": issue.severity.value,
            }
    return index


class AgentMemory:
    """Remembers previous QA runs under a history directory (#694)."""

    def __init__(self, history_dir: Path) -> None:
        self.history_dir = history_dir
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def load_history(self) -> list[QaReport]:
        """Stored reports, oldest first; unreadable files are skipped."""
        reports: list[QaReport] = []
        for path in sorted(self.history_dir.glob("qa_report_*.json")):
            try:
                reports.append(QaReport.model_validate_json(path.read_text()))
            except (ValueError, OSError):
                continue
        return reports

    def save_report(self, report: QaReport) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        path = self.history_dir / f"qa_report_{stamp}.json"
        path.write_text(report.model_dump_json(indent=2))
        return path

    def diff_reports(self, old: QaReport, new: QaReport) -> DiffReport:
        """Classify every issue across the two runs: new/persistent/resolved."""
        old_keys = _index_issues(old)
        new_keys = _index_issues(new)

        issues: list[DiffIssue] = []
        for key, fields in new_keys.items():
            status = "persistent" if key in old_keys else "new"
            issues.append(DiffIssue(**fields, status=status))
        for key, fields in old_keys.items():
            if key not in new_keys:
                issues.append(DiffIssue(**fields, status="resolved"))

        return DiffReport(
            old_generated_at=old.generated_at,
            new_generated_at=new.generated_at,
            issues=issues,
        )
