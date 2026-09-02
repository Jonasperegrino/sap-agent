"""CLI entry points (architecture D6): login, ask, discover.

Credentials: SAP_AGENT_URL / SAP_AGENT_USER env or flags; password from
SAP_AGENT_PASSWORD env or an interactive secure prompt. Never from argv.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from datetime import UTC, datetime

from playwright.sync_api import Page, sync_playwright

from .context import SessionContext
from .controller import AgentLoop, Candidate
from .memory import AgentMemory
from .schemas import Config, QaPageReport, QaReport, Severity, StepResult, StepStatus
from .tools.answer import answer_count_by_status, evaluate_question
from .tools.auth import AuthError, login, validate_app_url
from .tools.discover import discover_app
from .tools.extract import get_table_data
from .tools.network import NetworkCapture
from .tools.qa import QA_ROUTES, run_qa, write_qa_report
from .tools.report import classify_failure, collect_artifacts, should_retry, write_report
from .ui import terminal
from .ui.terminal import set_color_enabled

logger = logging.getLogger("fiori-agent")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fiori-agent", description="SAP Fiori discovery agent")
    parser.add_argument("--app", dest="app_url", help="Fiori app URL (env SAP_AGENT_URL)")
    parser.add_argument("--user", dest="username", help="username (env SAP_AGENT_USER)")
    parser.add_argument("--no-color", dest="no_color", action="store_true", help="disable ANSI terminal colors")
    parser.add_argument(
        "--timeout",
        dest="timeout_ms",
        type=int,
        default=None,
        help="base wait window in ms; overrides nav/extract/login timeouts (#676)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="log in and verify session")
    sub.add_parser("inspect", help="log in, capture network, dump table + trace")
    sub.add_parser("discover", help="log in and produce structured AppSummary JSON")
    ask = sub.add_parser("ask-status", help="count rows by status with evidence")
    ask.add_argument("status", help="status value to count (e.g. Approved)")
    askq = sub.add_parser("ask", help="answer a natural-language question")
    askq.add_argument("question", help="e.g. 'how many orders were built in 2026'")
    askq.add_argument(
        "--route",
        default=None,
        help="top-level page to answer against: dashboard | catalog | orders (default: current page)",
    )
    sub.add_parser("report", help="attempt login; on failure draft a bug report")
    qa = sub.add_parser("qa", help="run the full QA audit across every page (screenshot, a11y, UX)")
    qa.add_argument(
        "--format",
        dest="format",
        choices=("json", "markdown"),
        default=None,
        help="stdout output format (default: colored terminal summary); "
        "artifacts/qa_report.json + .md are always written",
    )
    sub.add_parser(
        "agent",
        help="planner-mode agent loop: pick login/audit actions until every route is audited (#693)",
    )
    return parser


def _resolve_config(args: argparse.Namespace) -> Config:
    from pydantic import SecretStr

    config = Config.from_env(app_url=args.app_url, username=args.username)
    if not config.has_credentials():
        if not config.username:
            config.username = input("Username: ").strip()
        if not config.password.get_secret_value():
            config.password = SecretStr(getpass.getpass("Password: "))
    validate_app_url(config.app_url)
    if getattr(args, "timeout_ms", None) is not None:
        base = max(args.timeout_ms, 1_000)
        config.login_timeout_ms = base
        config.nav_timeout_ms = base
        config.extract_timeout_ms = base
    return config


def cmd_login(config: Config) -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=config.headless)
        ctx = SessionContext(config)
        try:
            page = browser.new_page()
            result = login(page, config, ctx)
            logger.info(
                "login ok: %s (route %s, attempts %d)",
                result.landing_url,
                result.verified_route,
                result.attempts,
            )
            return 0
        except AuthError as exc:
            result = exc.result
            logger.error("login failed: %s (%s, attempts %d)", result.detail, result.kind.value, result.attempts)
            return 1
        finally:
            browser.close()


def cmd_inspect(config: Config) -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=config.headless)
        ctx = SessionContext(config)
        try:
            page = browser.new_page()
            capture = NetworkCapture(page, config.app_url)
            result = login(page, config, ctx)
            logger.info(
                "login ok: %s (route %s, attempts %d)",
                result.landing_url,
                result.verified_route,
                result.attempts,
            )
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
            logger.error("login failed: %s (%s, attempts %d)", result.detail, result.kind.value, result.attempts)
            return 1
        finally:
            browser.close()


def cmd_ask_status(config: Config, status: str) -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=config.headless)
        ctx = SessionContext(config)
        try:
            page = browser.new_page()
            capture = NetworkCapture(page, config.app_url)
            result = login(page, config, ctx)
            logger.info(
                "login ok: %s (route %s, attempts %d)",
                result.landing_url,
                result.verified_route,
                result.attempts,
            )
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
            logger.error("login failed: %s (%s, attempts %d)", result.detail, result.kind.value, result.attempts)
            return 1
        finally:
            browser.close()


def cmd_discover(config: Config) -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=config.headless)
        ctx = SessionContext(config)
        try:
            page = browser.new_page()
            capture = NetworkCapture(page, config.app_url)
            result = login(page, config, ctx)
            logger.info(
                "login ok: %s (route %s, attempts %d)",
                result.landing_url,
                result.verified_route,
                result.attempts,
            )
            summary = discover_app(page, capture, ctx, app_url=config.app_url)
            print(json.dumps(summary.model_dump(), indent=2))
            return 0
        except AuthError as exc:
            result = exc.result
            logger.error("login failed: %s (%s, attempts %d)", result.detail, result.kind.value, result.attempts)
            return 1
        finally:
            browser.close()


def cmd_ask(config: Config, question: str, route: str | None = None) -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=config.headless)
        ctx = SessionContext(config)
        try:
            page = browser.new_page()
            capture = NetworkCapture(page, config.app_url)
            result = login(page, config, ctx)
            logger.info(
                "login ok: %s (route %s, attempts %d)",
                result.landing_url,
                result.verified_route,
                result.attempts,
            )
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
                }.get(route, "salesTable"),
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
            logger.error("login failed: %s (%s, attempts %d)", result.detail, result.kind.value, result.attempts)
            return 1
        finally:
            browser.close()


def cmd_report(config: Config) -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=config.headless)
        ctx = SessionContext(config)
        try:
            page = browser.new_page()
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
            logger.error("login failed: %s (%s, attempts %d)", result.detail, result.kind.value, result.attempts)
            draft = collect_artifacts(page, ctx)
            draft.classification = classify_failure(result.kind.value)
            draft.title = f"Login failure ({result.kind.value}) — {config.app_url}"
            draft.actual = result.detail
            retryable = should_retry(result.kind.value)
            draft.reproduction_steps = [
                f"run: fiori-agent --app {config.app_url} report",
                f"login failed with `{result.kind.value}` after {result.attempts} attempts",
                f"retry policy: {'retryable (transient)' if retryable else 'fail-fast (deterministic)'}",
            ]
            path = write_report(draft, ctx)
            print(f"bug report: {path}")
            print(f"classification: {draft.classification.value}")
            return 1
        finally:
            browser.close()


def cmd_qa(config: Config, no_color: bool = False, fmt: str | None = None) -> int:
    set_color_enabled(not no_color)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=config.headless)
        ctx = SessionContext(config)
        try:
            page = browser.new_page()
            capture = NetworkCapture(page, config.app_url)
            if fmt is None:
                terminal.print_header("SAP Fiori QA Agent")
            result = login(page, config, ctx)
            logger.info(
                "login ok: %s (route %s, attempts %d)",
                result.landing_url,
                result.verified_route,
                result.attempts,
            )
            steps_total = len(QA_ROUTES) + 2  # login + dashboard/catalog/orders/customer
            if fmt is None:
                terminal.print_step(1, steps_total, "login ok — auditing app now")

            def on_progress(step: int, total: int, route: str) -> None:
                if fmt is None:
                    terminal.print_step(step, total, f"auditing {route}…")

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
            logger.error("login failed: %s (%s, attempts %d)", result.detail, result.kind.value, result.attempts)
            return 1
        finally:
            browser.close()


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


def cmd_agent(config: Config, no_color: bool = False) -> int:
    """Planner-mode agent loop (ADR D3/D4, #693): instead of a fixed script,
    the loop picks its own next action from an ordered candidate table —
    login first, then audit whichever top-level route is still unvisited —
    until every route is audited or the loop aborts (budget / stuck /
    non-retryable failure). Every decision is traced (`plan.decide.*`)."""
    from .tools.qa import _align_severities, _audit_page

    set_color_enabled(not no_color)
    terminal.print_header("SAP Fiori QA Agent — planner mode")
    collected: list[QaPageReport] = []

    def login_step(page: Page, ctx: SessionContext) -> StepResult:
        result = login(page, ctx.config, ctx)
        logger.info(
            "login ok: %s (route %s, attempts %d)",
            result.landing_url,
            result.verified_route,
            result.attempts,
        )
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

        def step(page: Page, ctx: SessionContext) -> StepResult:
            terminal.print_step(len(collected) + 2, steps_total + 1, f"auditing {route}…")
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
    for route in QA_ROUTES:
        candidates.append(
            Candidate(
                f"audit:{route}",
                applies=lambda history, r=route: (
                    _succeeded(history, "auth", "login") and not _succeeded(history, "qa", f"audit.{r}")
                ),
                step=audit_step(route),
                rationale=f"screenshot + accessibility/UX audit of {route}",
            )
        )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=config.headless)
        ctx = SessionContext(config)
        try:
            page = browser.new_page()
            capture = NetworkCapture(page, config.app_url)
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
        finally:
            browser.close()


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = _resolve_config(args)
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.command == "login":
        return cmd_login(config)
    if args.command == "inspect":
        return cmd_inspect(config)
    if args.command == "discover":
        return cmd_discover(config)
    if args.command == "ask-status":
        return cmd_ask_status(config, args.status)
    if args.command == "ask":
        return cmd_ask(config, args.question, route=args.route)
    if args.command == "report":
        return cmd_report(config)
    if args.command == "qa":
        return cmd_qa(config, no_color=args.no_color, fmt=args.format)
    if args.command == "agent":
        return cmd_agent(config, no_color=args.no_color)
    return 1


if __name__ == "__main__":
    sys.exit(main())
