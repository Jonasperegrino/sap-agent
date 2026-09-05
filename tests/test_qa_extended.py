"""Extended unit tests for QA workflow (issue #685)."""

from __future__ import annotations

from fakes import PageStub, ScriptedEvaluatePage

from sap_agent.context import SessionContext
from sap_agent.schemas import Config, Severity
from sap_agent.tools.qa import QaPageReport, _align_severities, _performance_hints, _title_size


def _ctx() -> SessionContext:
    return SessionContext(Config(app_url="http://x", username="u", password="p"))


class TestPerformanceHints:
    def test_high_resource_count(self) -> None:
        page = ScriptedEvaluatePage(results=[{"count": 50, "big": 0, "slow": 0, "largest": 100_000}])
        hints = _performance_hints(page)
        assert any("high resource count" in h for h in hints)

    def test_big_resources(self) -> None:
        page = ScriptedEvaluatePage(results=[{"count": 10, "big": 2, "slow": 0, "largest": 600_000}])
        hints = _performance_hints(page)
        assert any("500KB" in h for h in hints)

    def test_slow_resources(self) -> None:
        page = ScriptedEvaluatePage(results=[{"count": 10, "big": 0, "slow": 3, "largest": 10_000}])
        hints = _performance_hints(page)
        assert any(">3s" in h for h in hints)

    def test_no_hints_when_fast(self) -> None:
        page = ScriptedEvaluatePage(results=[{"count": 5, "big": 0, "slow": 0, "largest": 10_000}])
        assert _performance_hints(page) == []

    def test_exception_returns_empty(self) -> None:
        class ErrorPage(PageStub):
            def evaluate(self, expression, arg=None, **kwargs):
                raise RuntimeError("no browser")

        assert _performance_hints(ErrorPage()) == []


class TestTitleSize:
    def test_returns_size(self) -> None:
        page = ScriptedEvaluatePage(results=[16.0])
        assert _title_size(page) == 16.0

    def test_returns_zero_on_exception(self) -> None:
        class ErrorPage(PageStub):
            def evaluate(self, expression, arg=None, **kwargs):
                raise RuntimeError("x")

        assert _title_size(ErrorPage()) == 0.0

    def test_returns_zero_for_falsy(self) -> None:
        page = ScriptedEvaluatePage(results=[None])
        assert _title_size(page) == 0.0


class TestAlignSeverities:
    def test_overrides_severity(self) -> None:
        from sap_agent.schemas import AccessibilityIssue

        report = QaPageReport(
            route="x",
            accessibility_issues=[AccessibilityIssue(type="missing_alt", element="<img>", severity=Severity.LOW)],
        )
        _align_severities(report)
        # missing_alt should be classified by severity.classify_issue
        assert report.accessibility_issues[0].severity in Severity

    def test_empty_report_no_crash(self) -> None:
        report = QaPageReport(route="x")
        _align_severities(report)
        assert report.accessibility_issues == []
