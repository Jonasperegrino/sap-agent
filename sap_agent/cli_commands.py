"""CLI command handlers (split from cli.py): one function per subcommand.

Each handler opens a browser session, logs in, does its job, and returns a
process exit code. Reporting/planner tails shared between qa and agent live
here too.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .cli_runner import browser_session, log_auth_error, log_login
from .controller import AgentLoop, Candidate
from .memory import AgentMemory
from .schemas import (
    Config,
    QaPageReport,
    QaReport,
    Severity,
    StepResult,
    StepStatus,
)
from .tools.answer import answer_count_by_status, evaluate_question
from .tools.auth import AuthError, login
from .tools.discover import discover_app
from .tools.extract import get_table_data
from .tools.qa import QA_ROUTES, run_qa, write_qa_report
from .tools.report import classify_failure, collect_artifacts, should_retry, write_report
from .ui import terminal
from .ui.terminal import set_color_enabled

if TYPE_CHECKING:
    from .context import SessionContext
    from .protocols import PageLike

logger = logging.getLogger("fiori-agent")


def cmd_login(config: Config) -> int:  # pragma: no cover
    with browser_session(config) as (page, _capture, _ctx):
        try:
            result = login(page, config, _ctx)
            log_login(result, logger)
            return 0
        except AuthError as exc:
            result = exc.result
            log_auth_error(result, logger)
            return 1


def cmd_inspect(config: Config) -> int:  # pragma: no cover
    with browser_session(config) as (page, capture, ctx):
        try:
            result = login(page, config, ctx)
            log_login(result, logger)
            table = get_table_data(page, timeout_ms=config.extract_timeout_ms)
            captured_urls = capture.capture_response_urls()
            payload = capture.latest_response_body("sales.json")
            ctx.record(
                "extract",
                "table.dump",
                outcome=f"columns={len(table.columns)} rows={table.row_count}",
                url=page.url,
            )
            ctx.record(
                "network",
                "capture.summary",
                outcome=f"same-origin responses: {len(captured_urls)}",
            )
            for url in captured_urls:
                ctx.record("network", "capture.url", outcome="recorded", url=url)
            summary = {
                "table": table.to_dict(),
                "captured_urls": captured_urls,
                "sales_payload_matches_table": payload is not None,
            }

            print(json.dumps(summary, indent=2))
            return 0
        except AuthError as exc:
            result = exc.result
            log_auth_error(result, logger)
            return 1


def cmd_ask_status(config: Config, status: str) -> int:  # pragma: no cover
    with browser_session(config) as (page, capture, ctx):
        try:
            result = login(page, config, ctx)
            log_login(result, logger)
            answered = answer_count_by_status(
                page,
                status,
                ctx,
                endpoint=config.app_url,
            )
            payload = {
                "question": answered.question,
                "answer": answered.answer,
                "not_found": answered.not_found,
                "unsupported": answered.unsupported,
                "message": answered.message,
                "evidence": answered.evidence.model_dump(),
                "confidence": answered.confidence,
                "checksum": answered.checksum,
                "trace": ctx.snapshot(),
                "captured_urls": capture.capture_response_urls(),
            }
            print(json.dumps(payload, indent=2))
            return 0
        except AuthError as exc:
            result = exc.result
            log_auth_error(result, logger)
            return 1


def cmd_discover(config: Config) -> int:  # pragma: no cover
    with browser_session(config) as (page, capture, ctx):
        try:
            result = login(page, config, ctx)
            log_login(result, logger)
            summary = discover_app(page, capture, ctx, app_url=config.app_url)
            print(json.dumps(summary.model_dump(), indent=2))
            return 0
        except AuthError as exc:
            result = exc.result
            log_auth_error(result, logger)
            return 1


def cmd_ask(config: Config, question: str, route: str | None = None) -> int:  # pragma: no cover
    with browser_session(config) as (page, capture, ctx):
        try:
            result = login(page, config, ctx)
            log_login(result, logger)
            answered = evaluate_question(
                page,
                question,
                ctx,
                endpoint=config.app_url,
                route=route,
                app_url=config.app_url,
                capture=capture,
                source={
                    "catalog": "productTable",
                    "orders": "ordersTable",
                    "customers": "customersTable",
                    "customer": "customerOrdersTable",
                }.get(route or "", "salesTable"),
            )
            payload = {
                "question": answered.question,
                "intent": answered.intent.value,
                "answer": answered.answer,
                "not_found": answered.not_found,
                "unsupported": answered.unsupported,
                "message": answered.message,
                "evidence": answered.evidence.model_dump(),
                "confidence": answered.confidence,
                "follow_up": answered.follow_up,
                "checksum": answered.checksum,
                "trace": ctx.snapshot(),
                "captured_urls": capture.capture_response_urls(),
            }
            print(json.dumps(payload, indent=2))
            return 0
        except AuthError as exc:
            result = exc.result
            log_auth_error(result, logger)
            return 1


def cmd_report(config: Config) -> int:  # pragma: no cover
    with browser_session(config) as (page, _capture, ctx):
        try:
            result = login(page, config, ctx)
            logger.info(
                "login ok: %s (route %s, attempts %d) — nothing to report",
                result.landing_url,
                result.verified_route,
                result.attempts,
            )
            return 0
        except AuthError as exc:
            result = exc.result
            log_auth_error(result, logger)
            draft = collect_artifacts(page, ctx)
            draft.classification = classify_failure(result.kind_value())
            draft.title = f"Login failure ({result.kind_value()}) — {config.app_url}"
            draft.actual = result.detail
            retryable = should_retry(result.kind_value())
            draft.reproduction_steps = [
                f"run: fiori-agent --app {config.app_url} report",
                f"login failed with `{result.kind_value()}` after {result.attempts} attempts",
                f"retry policy: {'retryable (transient)' if retryable else 'fail-fast (deterministic)'}",
            ]
            path = write_report(draft, ctx)
            print(f"bug report: {path}")
            print(f"classification: {draft.classification.value}")
            return 1


def cmd_qa(config: Config, no_color: bool = False, fmt: str | None = None) -> int:  # pragma: no cover
    set_color_enabled(not no_color)
    with browser_session(config) as (page, capture, ctx):
        try:
            result = login(page, config, ctx)
            log_login(result, logger)
            steps_total = len(QA_ROUTES) + 2  # login + dashboard/catalog/orders/customer
            if fmt is None:
                terminal.print_step(1, steps_total, "login ok — auditing app now")

            _qa_start = time.monotonic()

            def on_progress(step: int, total: int, route: str) -> None:
                if fmt is None:
                    terminal.print_step(step, total, f"auditing {route}…", elapsed_s=time.monotonic() - _qa_start)

            report = run_qa(
                page,
                capture,
                ctx,
                app_url=config.app_url,
                progress=on_progress,
                step_offset=1,
            )
            _persist_and_emit_qa(report, ctx, fmt=fmt)
            return 0
        except AuthError as exc:
            result = exc.result
            log_auth_error(result, logger)
            return 1


def _persist_and_emit_qa(report: QaReport, ctx: SessionContext, *, fmt: str | None) -> str:
    """Write artifacts, update the run history, and render the finished report.

    Shared tail of `qa` and `agent` — both produce a QaReport and differ only
    in how it was assembled (fixed walk vs planner loop).
    """
    path = write_qa_report(report, ctx)
    md_path = ctx.artifact_path("qa_report.md")
    md_path.write_text(report.to_markdown())

    memory = AgentMemory(ctx.artifact_path("history"))
    previous = memory.load_history()
    if previous:
        diff = memory.diff_reports(previous[-1], report)
        status_counts = diff.counts_by_status()
        logger.info(
            "diff vs previous run (%s): new=%d persistent=%d resolved=%d",
            previous[-1].generated_at or "unknown time",
            status_counts["new"],
            status_counts["persistent"],
            status_counts["resolved"],
        )
    memory.save_report(report)

    counts = report.counts_by_severity()
    logger.info(
        "qa summary: pages=%d issues=%d high=%d medium=%d low=%d",
        len(report.pages),
        report.total_issues,
        counts[Severity.HIGH.value],
        counts[Severity.MEDIUM.value],
        counts[Severity.LOW.value],
    )
    if fmt == "json":
        print(report.model_dump_json(indent=2))
    elif fmt == "markdown":
        print(report.to_markdown(), end="")
    else:
        for page_report in report.pages:
            for issue in page_report.accessibility_issues:
                terminal.print_issue("a11y", issue.type, issue.severity, issue.element)
            for issue in page_report.ux_issues:
                terminal.print_issue("ux", issue.type, issue.severity, issue.element)
            for hint in page_report.performance_hints:
                print(f"  [PERF] {hint}")
        terminal.print_summary(report)
        terminal.print_report_path(path)
    return path


def _succeeded(history: list[StepResult], tool: str, action: str) -> bool:
    """True when `tool.action` already succeeded in the planner history."""
    return any(r.tool == tool and r.action == action and r.status == StepStatus.SUCCESS for r in history)


def cmd_agent(config: Config, no_color: bool = False) -> int:  # pragma: no cover
    """Planner-mode agent loop (ADR D3/D4, #693): instead of a fixed script,
    the loop picks its own next action from an ordered candidate table —
    login first, then audit whichever top-level route is still unvisited —
    until every route is audited or the loop aborts (budget / stuck /
    non-retryable failure). Every decision is traced (`plan.decide.*`)."""
    from .tools.qa import _align_severities, _audit_page

    set_color_enabled(not no_color)
    terminal.print_header("SAP Fiori QA Agent — planner mode")
    collected: list[QaPageReport] = []

    def login_step(page: PageLike, ctx: SessionContext) -> StepResult:
        result = login(page, ctx.config, ctx)
        log_login(result, logger)
        return StepResult(
            tool="auth",
            action="login",
            status=StepStatus.SUCCESS,
            outcome=f"route={result.verified_route}",
            url=result.landing_url,
            payload=result.verified_route,
        )

    def audit_step(route: str):
        steps_total = len(QA_ROUTES) + 1
        _start = time.monotonic()

        def step(page: PageLike, ctx: SessionContext) -> StepResult:
            terminal.print_step(
                len(collected) + 2, steps_total + 1, f"auditing {route}…", elapsed_s=time.monotonic() - _start
            )
            page_report = _audit_page(page, route, ctx, capture)
            collected.append(page_report)
            issues = len(page_report.accessibility_issues) + len(page_report.ux_issues)
            return StepResult(
                tool="qa",
                action=f"audit.{route}",
                status=StepStatus.SUCCESS,
                outcome=f"issues={issues}",
                url=page.url,
                payload=route,
            )

        return step

    candidates = [
        Candidate(
            "login",
            applies=lambda history: not _succeeded(history, "auth", "login"),
            step=login_step,
            rationale="establish an authenticated session",
        ),
    ]
    candidates.extend(
        Candidate(
            f"audit:{route}",
            applies=lambda history, r=route: (
                _succeeded(history, "auth", "login") and not _succeeded(history, "qa", f"audit.{r}")
            ),
            step=audit_step(route),
            rationale=f"screenshot + accessibility/UX audit of {route}",
        )
        for route in QA_ROUTES
    )

    with browser_session(config) as (page, capture, ctx):
        try:
            loop = AgentLoop(config, page, ctx)
            result = loop.run_planned(
                "audit every top-level route",
                candidates,
                goal_met=lambda history: all(_succeeded(history, "qa", f"audit.{r}") for r in QA_ROUTES),
            )
            if not result.success:
                kind = result.failure_kind.value if result.failure_kind else "unknown"
                logger.error(
                    "agent aborted after %d/%d steps: %s (%s)",
                    result.steps_used,
                    result.budget,
                    result.reason,
                    kind,
                )
                return 1
            for page_report in collected:
                _align_severities(page_report)
            report = QaReport(
                app_url=config.app_url,
                generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
                pages=collected,
            )
            _persist_and_emit_qa(report, ctx, fmt=None)
            return 0
        except AuthError as exc:
            result = exc.result
            log_auth_error(result, logger)
            return 1
