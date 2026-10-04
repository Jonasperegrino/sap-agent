"""Bug-report drafting + failure classification (issue #648).

Reproducible artifacts (URL, page state, trace tail, screenshot, timestamp) are
collected on failure and rendered into a secret-free markdown bug ticket.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..schemas import (
    AuthFailureKind,
    BugReport,
    FailureClass,
    FailureKind,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ..context import SessionContext
    from ..protocols import PageLike

logger = logging.getLogger(__name__)

#: failure-kind → blame. Auth kinds map from the #645 taxonomy.
CLASSIFICATION_MATRIX: dict[str, FailureClass] = {
    FailureKind.NAV_LOOP.value: FailureClass.PRODUCT_BUG,
    FailureKind.INCONSISTENT_LOAD.value: FailureClass.PRODUCT_BUG,
    FailureKind.EMPTY_STATE.value: FailureClass.PRODUCT_BUG,
    FailureKind.BACKEND_ERROR.value: FailureClass.PRODUCT_BUG,
    # ponytail: hardcoded matrix. Full module if false positives matter.
    "toast_or_empty": FailureClass.PRODUCT_BUG,
    "blocker": FailureClass.PRODUCT_BUG,
    FailureKind.SELECTOR_FAILURE.value: FailureClass.AGENT_LIMITATION,
    FailureKind.AGENT_LIMITATION.value: FailureClass.AGENT_LIMITATION,
    AuthFailureKind.REDIRECT_LOOP.value: FailureClass.PRODUCT_BUG,
    AuthFailureKind.TIMEOUT.value: FailureClass.AGENT_LIMITATION,
    AuthFailureKind.NETWORK_ERROR.value: FailureClass.AGENT_LIMITATION,
    AuthFailureKind.ELEMENT_NOT_FOUND.value: FailureClass.AGENT_LIMITATION,
    AuthFailureKind.BAD_CREDENTIALS.value: FailureClass.UNSUPPORTED_AUTH_FLOW,
    AuthFailureKind.SSO_UNSUPPORTED.value: FailureClass.UNSUPPORTED_AUTH_FLOW,
}

#: failure kinds that warrant a bounded retry (others fail fast — see auth.py)
RETRYABLE_KINDS = frozenset(
    {
        AuthFailureKind.TIMEOUT.value,
        AuthFailureKind.NETWORK_ERROR.value,
        AuthFailureKind.ELEMENT_NOT_FOUND.value,
        FailureKind.EMPTY_STATE.value,
        FailureKind.INCONSISTENT_LOAD.value,
    }
)


def classify_failure(kind: str) -> FailureClass:
    """Map a failure kind to the blame class (unknown → agent limitation)."""
    return CLASSIFICATION_MATRIX.get(kind, FailureClass.AGENT_LIMITATION)


def should_retry(kind: str) -> bool:
    """Retry only transient, state-free failures; nav loops and bad creds stop."""
    return kind in RETRYABLE_KINDS


def collect_artifacts(
    page: PageLike,
    ctx: SessionContext,
    *,
    screenshot_name: str = "failure.png",
) -> BugReport:
    """Capture reproducible state into a draft report (no credentials)."""
    from ..tools.extract import get_all_tables

    tables = get_all_tables(page, timeout_ms=3_000)
    visible = [f"table[{i + 1}] columns={t.columns} rows={t.row_count}" for i, t in enumerate(tables)] or [
        "no table rendered"
    ]

    screenshot = ctx.artifact_path(screenshot_name)
    with screenshot.open("wb") as fh:
        fh.write(page.screenshot())

    now = datetime.now(UTC).isoformat(timespec="seconds")
    return BugReport(
        title=f"{ctx.config.app_url} — agent stuck",
        expected="agent completes the step and proceeds to the next goal",
        actual=(f"agent could not complete the step; visible state: {', '.join(visible)}"),
        reproduction_steps=[
            f"run the agent against {ctx.config.app_url}",
            "reproduce the failing step (see trace tail)",
            "observe the failure classification",
        ],
        environment={
            "app_url": ctx.config.app_url,
            "browser": "chromium",
            "timestamp": now,
            "agent_version": "sap-poc",
        },
        artifacts=[str(screenshot)],
        trace_tail=[e.model_dump_json() for e in ctx.trace[-10:]],
        secret_values=[
            ctx.config.username,
            ctx.config.password.get_secret_value() if ctx.config.password else "",
        ],
    )


def write_report(report: BugReport, ctx: SessionContext, *, name: str = "bug_report.md") -> Path:
    """Persist the markdown report next to its artifacts."""
    path = ctx.artifact_path(name)
    report.artifacts = [str(p) for p in ctx.artifacts_dir.iterdir()]
    path.write_text(report.to_markdown())
    logger.info("bug report written to %s", path)
    return path


def write_sweep_reports(report, out_dir: Path, *, verdict: str) -> tuple[Path, list[Path]]:
    """WP4 output: summary.md + one md per issue (screenshot, repro, expected/actual,
    severity+confidence+provider, classify_failure)."""
    from pathlib import Path

    out = out_dir if isinstance(out_dir, Path) else Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    counts = report.counts_by_severity()
    summary = out / "summary.md"
    lines = [
        f"# Sweep summary — verdict: {verdict}",
        "",
        f"Pages: {len(report.pages)} · Issues: {report.total_issues} "
        f"(blocker {counts.get('blocker', 0)}, high {counts.get('high', 0)}, "
        f"medium {counts.get('medium', 0)}, low {counts.get('low', 0)})",
        "",
    ]
    for page in report.pages:
        lines.append(f"## {page.route}")
        lines.extend(
            f"- [{src}/{issue.severity.value}] {issue.type}: {issue.element}"
            for src, issues in (("a11y", page.accessibility_issues), ("ux", page.ux_issues))
            for issue in issues
        )
        lines.append("")
    summary.write_text("\n".join(lines).rstrip() + "\n")
    paths: list[Path] = []
    for page in report.pages:
        for src, issues in (("a11y", page.accessibility_issues), ("ux", page.ux_issues)):
            for issue in issues:
                slug = f"{page.route}-{src}-{issue.type}".replace("/", "-").replace(" ", "_")
                p = out / f"{slug}.md"
                shot = getattr(issue, "screenshot", "") or (page.screenshots[0].path if page.screenshots else "none")
                p.write_text(
                    f"# {issue.type} on {page.route}\n\n"
                    f"Severity: {issue.severity.value} · confidence: high · provider: rules\n"
                    f"Classification: `{classify_failure(issue.type).value}`\n\n"
                    f"## Expected\n{issue.suggestion}\n\n"
                    f"## Actual\n{issue.type} at {issue.element}\n\n"
                    f"## Reproduction steps\n1. run: fiori-agent qa\n2. open route {page.route}\n"
                    f"3. observe {issue.type}\n\n"
                    f"## Screenshot\n- `{shot}`\n"
                )
                paths.append(p)
    return summary, paths
