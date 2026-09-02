"""Streamlit-facing orchestration for one isolated agent run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from ..context import SessionContext
from ..schemas import AnsweredQuestion, BugReport, Config
from ..tools.answer import evaluate_question
from ..tools.auth import AuthError, login
from ..tools.network import NetworkCapture
from ..tools.report import classify_failure, collect_artifacts, write_report


@dataclass
class RunResult:
    """Sanitized result returned to the Streamlit page."""

    answer: AnsweredQuestion | None = None
    report: BugReport | None = None
    report_path: Path | None = None
    trace: list[dict[str, Any]] | None = None
    error: str = ""


def _launch_args() -> dict:
    """Chromium args for Streamlit Cloud / sandboxed envs."""
    import os

    # Streamlit Cloud runs as non-root without sandbox
    if os.environ.get("STREAMLIT_RUNTIME") or os.environ.get("STREAMLIT_CLOUD"):
        return {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    return {}


def run_question(config: Config, question: str, route: str | None = None) -> RunResult:
    """Answer a question and automatically draft a report when the run fails."""
    ctx = SessionContext(config)
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
                source={"catalog": "productTable", "orders": "ordersTable"}.get(route, "salesTable"),
            )
            return RunResult(answer=answer, trace=ctx.snapshot())
        except AuthError as exc:
            return _failure_result(page, ctx, exc.result.kind.value, exc.result.detail)
        except (PlaywrightError, TimeoutError) as exc:
            return _failure_result(page, ctx, "agent_limitation", str(exc)[:300])
        finally:
            browser.close()


def _failure_result(page: Any, ctx: SessionContext, kind: str, detail: str) -> RunResult:
    """Collect and persist a secret-free report for a failed run."""
    report = collect_artifacts(page, ctx)
    report.classification = classify_failure(kind)
    report.title = f"Agent failure ({kind}) — {ctx.config.app_url}"
    report.actual = detail or report.actual
    path = write_report(report, ctx)
    return RunResult(report=report, report_path=path, trace=ctx.snapshot(), error=detail)
