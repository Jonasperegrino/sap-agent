"""Streamlit-facing orchestration for one isolated agent run."""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from ..browser import launch_args
from ..context import SessionContext
from ..tools.answer import evaluate_question
from ..tools.auth import AuthError, login
from ..tools.network import NetworkCapture
from ..tools.report import classify_failure, collect_artifacts, write_report

if TYPE_CHECKING:
    from pathlib import Path

    from ..schemas import AnsweredQuestion, BugReport, Config

logger = logging.getLogger("fiori-agent")


@dataclass
class RunResult:
    """Sanitized result returned to the Streamlit page."""

    answer: AnsweredQuestion | None = None
    report: BugReport | None = None
    report_path: Path | None = None
    trace: list[dict[str, Any]] | None = None
    error: str = ""


def run_question(config: Config, question: str, route: str | None = None) -> RunResult:
    """Answer a question and automatically draft a report when the run fails."""
    logger.info("agent question (route=%s): %s", route, question)
    ctx = SessionContext(config)

    def _run_once() -> RunResult:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=config.headless, **launch_args())
            page = browser.new_page()
            capture = NetworkCapture(page, config.app_url)
            try:
                login(page, config, ctx)
                answer = evaluate_question(
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
                return RunResult(answer=answer, trace=ctx.snapshot())
            except AuthError as exc:
                return _failure_result(page, ctx, exc.result.kind_value(), exc.result.detail)
            except (PlaywrightError, TimeoutError) as exc:
                return _failure_result(page, ctx, "agent_limitation", str(exc)[:300])
            finally:
                with contextlib.suppress(PlaywrightError, AttributeError, OSError):
                    browser.close()

    try:
        return _run_once()
    except (PlaywrightError, OSError, TimeoutError) as exc:
        msg = str(exc)
        # Fail fast when the browser binary is missing. Installing here
        # (`playwright install` downloads ~150MB synchronously) blocks the
        # Streamlit session for minutes with no progress, which drops the
        # websocket and blanks the page. The binary must come from build
        # time (.streamlit/setup.sh); the UI gates on _chromium_ready so
        # this path should only fire when provisioning failed.
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            logger.error("browser binary missing: %s", msg[:300])
            detail = (
                "Browser binary missing — Chromium provisioning failed. "
                "Redeploy so `.streamlit/setup.sh` installs it at build time, "
                "then retry."
            )
        else:
            detail = msg[:300]
            # fallback — browser never started, no screenshot possible
            logger.error("agent run failed without browser: %s", detail)
        from sap_agent.schemas import BugReport

        report = BugReport(
            title=f"Agent failure — {config.app_url}",
            actual=detail,
            artifacts=[],
            trace_tail=[e.model_dump_json() for e in ctx.trace[-10:]],
        )
        return RunResult(report=report, trace=ctx.snapshot(), error=detail)


def _failure_result(page: Any, ctx: SessionContext, kind: str, detail: str) -> RunResult:
    """Collect and persist a secret-free report for a failed run."""
    report = collect_artifacts(page, ctx)
    report.classification = classify_failure(kind)
    report.title = f"Agent failure ({kind}) — {ctx.config.app_url}"
    report.actual = detail or report.actual
    path = write_report(report, ctx)
    return RunResult(report=report, report_path=path, trace=ctx.snapshot(), error=detail)
