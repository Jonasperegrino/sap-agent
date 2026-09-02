"""Unit tests for failure classification + bug-report drafting (issue #648)."""

from __future__ import annotations

from sap_agent.context import SessionContext
from sap_agent.schemas import (
    AuthFailureKind,
    BugReport,
    Config,
    FailureClass,
    FailureKind,
)
from sap_agent.tools.report import classify_failure, should_retry


class FakeCtx(SessionContext):
    def __init__(self) -> None:
        super().__init__(Config(app_url="http://localhost:8080", username="demo", password="x"))
        self.record("auth", "login.start", "navigating", url="http://localhost:8080")
        self.record("auth", "login.failed", "bad_credentials", url="http://localhost:8080")
        self.record("answer", "count_where", "answered", detail="matched 2 rows")

    def artifact_path(self, name: str):
        return self.artifacts_dir / name


class TestClassificationMatrix:
    def test_product_bug_kinds(self) -> None:
        kinds = (
            FailureKind.NAV_LOOP,
            FailureKind.INCONSISTENT_LOAD,
            FailureKind.EMPTY_STATE,
            FailureKind.BACKEND_ERROR,
        )
        for kind in kinds:
            assert classify_failure(kind.value) == FailureClass.PRODUCT_BUG

    def test_agent_limitation_kinds(self) -> None:
        for kind in (FailureKind.SELECTOR_FAILURE, FailureKind.AGENT_LIMITATION, AuthFailureKind.TIMEOUT):
            assert classify_failure(kind.value) == FailureClass.AGENT_LIMITATION

    def test_unsupported_auth_flow(self) -> None:
        assert classify_failure(AuthFailureKind.BAD_CREDENTIALS.value) == FailureClass.UNSUPPORTED_AUTH_FLOW
        assert classify_failure(AuthFailureKind.SSO_UNSUPPORTED.value) == FailureClass.UNSUPPORTED_AUTH_FLOW

    def test_unknown_kind_defaults_to_agent_limitation(self) -> None:
        assert classify_failure("alien_kind") == FailureClass.AGENT_LIMITATION

    def test_retry_policy(self) -> None:
        assert should_retry(AuthFailureKind.TIMEOUT.value)
        assert should_retry(FailureKind.EMPTY_STATE.value)
        assert not should_retry(FailureKind.NAV_LOOP.value)
        assert not should_retry(AuthFailureKind.BAD_CREDENTIALS.value)


class TestBugReport:
    def test_markdown_renders_all_sections(self) -> None:
        report = BugReport(
            title="Login failure (bad_credentials) — app",
            expected="agent logs in and reaches the dashboard",
            actual="credentials rejected",
            classification=FailureClass.UNSUPPORTED_AUTH_FLOW,
            reproduction_steps=["run agent", "observe failure"],
            environment={"app_url": "http://localhost:8080", "timestamp": "2026-01-01T00:00:00+00:00"},
            artifacts=["/tmp/failure.png"],
            trace_tail=['{"tool":"auth","action":"login.failed"}'],
        )
        md = report.to_markdown()
        sections = (
            "# Login failure",
            "**Classification**",
            "## Expected",
            "## Actual",
            "## Reproduction steps",
            "## Environment",
            "## Attachments",
            "## Last agent actions",
        )
        for section in sections:
            assert section in md
        assert "bad_credentials" in md

    def test_markdown_empty_attachments_placeholder(self) -> None:
        report = BugReport(title="t", expected="e", actual="a")
        assert "- none" in report.to_markdown()

    def test_report_markdown_contains_no_secret(self) -> None:
        ctx = FakeCtx()
        ctx.record("x", "y", "password123", detail="wrote password123 here")
        report = BugReport(
            title="t",
            expected="e",
            actual="a",
            trace_tail=[e.model_dump_json() for e in ctx.trace],
            secret_values=["password123"],
        )
        md = report.to_markdown()
        assert "password123" not in md
        assert "[REDACTED]" in md
