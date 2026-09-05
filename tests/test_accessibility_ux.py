"""Unit tests for accessibility audit + UX critique (issues #687, #688)."""

from __future__ import annotations

from fakes import PageStub, ScriptedEvaluatePage

from sap_agent.schemas import Severity
from sap_agent.tools.accessibility import audit_accessibility
from sap_agent.tools.ux_critique import critique_ux


class TestAuditAccessibility:
    def test_returns_empty_on_exception(self) -> None:
        # Force exception by making evaluate raise
        class ErrorPage(PageStub):
            def evaluate(self, expression: str, arg=None, **kwargs):
                raise RuntimeError("browser gone")

        assert audit_accessibility(ErrorPage()) == []

    def test_empty_result_returns_empty_list(self) -> None:
        page = ScriptedEvaluatePage(results=[[]])
        assert audit_accessibility(page) == []

    def test_parses_issues_from_evaluate(self) -> None:
        raw_issues = [
            {"type": "missing_alt", "element": "<img>", "severity": "high", "suggestion": "add alt"},
            {"type": "contrast", "element": "<p>", "severity": "medium", "suggestion": "fix contrast"},
        ]
        page = ScriptedEvaluatePage(results=[raw_issues])
        issues = audit_accessibility(page)
        assert len(issues) == 2
        assert issues[0].type == "missing_alt"
        assert issues[0].severity == Severity.HIGH
        assert issues[1].type == "contrast"
        assert issues[1].severity == Severity.MEDIUM

    def test_default_severity_is_low(self) -> None:
        raw = [{"type": "x", "element": "<el>"}]
        page = ScriptedEvaluatePage(results=[raw])
        issues = audit_accessibility(page)
        assert issues[0].severity == Severity.LOW

    def test_script_is_called(self) -> None:
        page = ScriptedEvaluatePage(results=[[]])
        audit_accessibility(page)
        assert len(page.expressions) == 1
        assert "sapMPage" in page.expressions[0]


class TestCritiqueUx:
    def test_returns_empty_on_exception(self) -> None:
        class ErrorPage(PageStub):
            def evaluate(self, expression: str, arg=None, **kwargs):
                raise TimeoutError("slow")

        assert critique_ux(ErrorPage()) == []

    def test_empty_result(self) -> None:
        page = ScriptedEvaluatePage(results=[[]])
        assert critique_ux(page) == []

    def test_parses_ux_issues(self) -> None:
        raw = [
            {"type": "spacing_inconsistency", "element": "buttons", "severity": "low", "suggestion": "fix spacing"},
            {"type": "visual_hierarchy", "element": ".sapMTitle", "severity": "medium", "suggestion": "bigger title"},
        ]
        page = ScriptedEvaluatePage(results=[raw])
        issues = critique_ux(page)
        assert len(issues) == 2
        assert issues[0].type == "spacing_inconsistency"
        assert issues[1].severity == Severity.MEDIUM

    def test_script_is_called(self) -> None:
        page = ScriptedEvaluatePage(results=[[]])
        critique_ux(page)
        assert len(page.expressions) == 1
        assert "sapMPage" in page.expressions[0]
