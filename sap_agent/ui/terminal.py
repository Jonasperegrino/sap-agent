"""Terminal output formatting for the demo CLI (#697).

Color-coded, icon-tagged, box-drawn output on stdout. Colors auto-disable when
stdout is not a TTY or --no-color is passed; the plain JSON report artifact is
never touched by this module.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from ..schemas import QaReport, Severity

_CODE = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "green": "\033[32m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
    "magenta": "\033[35m",
}

_SEVERITY_COLOR = {
    Severity.HIGH.value: "red",
    Severity.MEDIUM.value: "yellow",
    Severity.LOW.value: "blue",
}

_enabled = sys.stdout.isatty()


def set_color_enabled(value: bool) -> None:
    global _enabled
    _enabled = value


def _paint(text: str, color: str = "") -> str:
    if not _enabled or not color:
        return text
    return f"{_CODE[color]}{text}{_CODE['reset']}"


def print_header(title: str) -> None:
    width = max(len(title), 40) + 4
    print(_paint("═" * width, "cyan"))
    print(_paint(f"  {title}", "bold") + " " * (width - len(title) - 2))
    print(_paint("═" * width, "cyan"))


def print_step(step: int, total: int, text: str) -> None:
    print(_paint(f"step {step}/{total}", "cyan") + f"  {text}")


def print_issue(category: str, issue_type: str, severity: Severity, element: str) -> None:
    tag = _paint(f"[{category.upper()}]", _SEVERITY_COLOR.get(severity.value, ""))
    level = _paint(f"{severity.value.upper():6s}", _SEVERITY_COLOR.get(severity.value, ""))
    print(f"  {tag} {level} {issue_type:22s} {element}")


def _box(title: str, rows: Sequence[tuple[str, str]]) -> None:
    text_col = max([len(title) - 2] + [len(a) for a, _ in rows] + [len(b) for _, b in rows])
    stretch = max(text_col, 24)
    line = "─" * (stretch + 8)
    print("┌" + line + "┐")
    print("│ " + _paint(f"{title:<{stretch + 3}}", "bold") + " │")
    print("├" + line + "┤")
    for a, b in rows:
        print(f"│ {a:<{stretch}} │ {b:<3} │")
    print("└" + line + "┘")


def print_summary(report: QaReport) -> None:
    counts = report.counts_by_severity()
    total = report.total_issues
    header = f"Found {total} issue{'s' if total != 1 else ''}: "
    header += ", ".join(f"{counts[s.value]} {s.value}" for s in Severity)
    print_header(header)

    a11y = sum(len(p.accessibility_issues) for p in report.pages)
    ux = sum(len(p.ux_issues) for p in report.pages)
    perf = sum(len(p.performance_hints) for p in report.pages)
    _box(
        "Issues by category",
        [
            ("accessibility", str(a11y)),
            ("UX", str(ux)),
            ("performance", str(perf)),
        ],
    )

    severity_rows = [(s.value.capitalize(), str(counts[s.value])) for s in Severity]
    _box("Issues by severity", severity_rows)


def print_report_path(path: str) -> None:
    print(_paint(f"report: {path}", "green"))
