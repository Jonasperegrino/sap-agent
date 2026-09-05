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


def _launch_args() -> dict:
    """Chromium args for Streamlit Cloud / sandboxed envs (see browser.launch_args)."""
    return launch_args()


def run_question(config: Config, question: str, route: str | None = None) -> RunResult:
    """Answer a question and automatically draft a report when the run fails."""
    logger.info("agent question (route=%s): %s", route, question)
    ctx = SessionContext(config)

    def _run_once() -> RunResult:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=config.headless, **_launch_args())
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
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            import subprocess as _sp

            logger.warning("browser missing, attempting playwright install: %s", msg[:200])
            for _cmd in (
                ["playwright", "install", "chromium"],
                ["playwright", "install", "chromium-headless-shell"],
                ["python", "-m", "playwright", "install", "chromium"],
            ):
                try:
                    _sp.run(_cmd, check=False, timeout=180)
                except (OSError, _sp.SubprocessError):
                    continue
                try:
                    return _run_once()
                except (PlaywrightError, OSError, TimeoutError):
                    continue
        # fallback — browser never started, no screenshot possible
        from sap_agent.schemas import BugReport

        logger.error("agent run failed without browser: %s", msg[:300])
        report = BugReport(
            title=f"Agent failure — {config.app_url}",
            actual=msg[:300],
            artifacts=[],
            trace_tail=[e.model_dump_json() for e in ctx.trace[-10:]],
        )
        return RunResult(report=report, trace=ctx.snapshot(), error=msg[:300])


def _failure_result(page: Any, ctx: SessionContext, kind: str, detail: str) -> RunResult:
    """Collect and persist a secret-free report for a failed run."""
    report = collect_artifacts(page, ctx)
    report.classification = classify_failure(kind)
    report.title = f"Agent failure ({kind}) — {ctx.config.app_url}"
    report.actual = detail or report.actual
    path = write_report(report, ctx)
    return RunResult(report=report, report_path=path, trace=ctx.snapshot(), error=detail)
