"""Extended unit tests for controller (issue #684): _run_step exception handling."""

from __future__ import annotations

from playwright.sync_api import Error as PlaywrightError

from sap_agent.context import SessionContext
from sap_agent.schemas import AuthFailureKind, AuthResult, Config, StepStatus
from sap_agent.tools.auth import AuthError
from sap_agent.tools.report import should_retry


class FakePage:
    def __init__(self) -> None:
        self.url = "http://localhost:8080/#/dashboard"


class TestRunStep:
    def _loop(self):
        from sap_agent.controller import AgentLoop

        config = Config(app_url="http://x", username="u", password="p")
        ctx = SessionContext(config)
        return AgentLoop(config, FakePage(), ctx)

    def test_auth_error_returns_failure(self) -> None:
        loop = self._loop()
        result = AuthResult(ok=False, kind=AuthFailureKind.BAD_CREDENTIALS, detail="bad creds", attempts=1)

        def step(page, ctx):
            raise AuthError(result)

        r = loop._run_step(step)
        assert r.status == StepStatus.FAILURE
        assert r.outcome == "bad_credentials"
        assert r.detail == "bad creds"
        assert r.transient is False

    def test_auth_error_transient(self) -> None:
        loop = self._loop()
        result = AuthResult(ok=False, kind=AuthFailureKind.TIMEOUT, attempts=1)

        def step(page, ctx):
            raise AuthError(result)

        r = loop._run_step(step)
        assert r.status == StepStatus.FAILURE
        assert r.outcome == "timeout"
        assert r.transient is True

    def test_playwright_error_returns_failure(self) -> None:
        loop = self._loop()

        def step(page, ctx):
            raise PlaywrightError("element not found")

        r = loop._run_step(step)
        assert r.status == StepStatus.FAILURE
        assert r.outcome == "exception"
        assert "element not found" in r.detail
        assert r.transient is True

    def test_timeout_error_returns_failure(self) -> None:
        loop = self._loop()

        def step(page, ctx):
            raise TimeoutError("timed out")

        r = loop._run_step(step)
        assert r.status == StepStatus.FAILURE
        assert r.outcome == "exception"
        assert r.transient is True


class TestShouldRetryExtended:
    def test_retryable_kinds(self) -> None:
        assert should_retry("timeout")
        assert should_retry("network_error")
        assert should_retry("element_not_found")
        assert should_retry("empty_state")
        assert should_retry("inconsistent_load")

    def test_non_retryable_kinds(self) -> None:
        assert not should_retry("nav_loop")
        assert not should_retry("bad_credentials")
        assert not should_retry("sso_unsupported")
        assert not should_retry("redirect_loop")
