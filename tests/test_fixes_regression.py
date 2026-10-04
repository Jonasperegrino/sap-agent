"""Regression tests for code-quality audit fixes."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sap_agent.context import SessionContext
from sap_agent.controller import AgentLoop, Candidate
from sap_agent.memory import AgentMemory
from sap_agent.schemas import Config, QaReport, StepResult, StepStatus

if TYPE_CHECKING:
    from sap_agent.protocols import PageLike


def _config() -> Config:
    return Config(app_url="http://localhost:8080", username="demo", password="x", retry_budget=2)


class _FakePage:
    def goto(self, url, *, wait_until=None, **kwargs) -> None:
        self.last_goto = url


class TestPlannerSuccessfulFinalRetry:
    def test_third_consecutive_success_reaches_goal(self) -> None:
        """Same candidate 3x where 3rd succeeds + meets goal must succeed, not NAV_LOOP."""
        ctx = SessionContext(_config())
        loop = AgentLoop(_config(), cast("PageLike", _FakePage()), ctx, stuck_threshold=3)
        calls = 0

        def step(_page, _ctx):
            nonlocal calls
            calls += 1
            if calls < 3:
                return StepResult(
                    tool="qa", action="audit.x", status=StepStatus.FAILURE, outcome="boom", transient=True
                )
            return StepResult(tool="qa", action="audit.x", status=StepStatus.SUCCESS, outcome="ok")

        candidates = [Candidate("audit:x", applies=lambda _h: True, step=step)]
        result = loop.run_planned("goal", candidates, goal_met=lambda h: len(h) >= 3)
        assert result.success
        assert result.steps_used == 3
        assert calls == 3


class TestHistoryFilenameCollision:
    def test_rapid_saves_do_not_overwrite(self, tmp_path) -> None:
        memory = AgentMemory(tmp_path / "history")
        r1 = QaReport(app_url="http://x", generated_at="t1")
        r2 = QaReport(app_url="http://x", generated_at="t2")
        p1 = memory.save_report(r1)
        p2 = memory.save_report(r2)
        assert p1 != p2
        assert p1.exists() and p2.exists()

    def test_load_history_limit_newest(self, tmp_path) -> None:
        memory = AgentMemory(tmp_path / "history")
        memory.save_report(QaReport(app_url="http://x", generated_at="t1"))
        memory.save_report(QaReport(app_url="http://x", generated_at="t2"))
        latest = memory.load_history(limit=1)
        assert len(latest) == 1
        assert latest[0].generated_at == "t2"


class TestInstallerGuard:
    def test_second_start_is_noop(self, monkeypatch) -> None:
        import sap_agent.browser as browser

        monkeypatch.setattr(browser, "try_install_chromium", lambda *a, **k: None)
        monkeypatch.setattr(browser, "_installer_started", False)
        assert browser.ensure_chromium_install_started() is True
        assert browser.ensure_chromium_install_started() is False
        monkeypatch.setattr(browser, "_installer_started", False)
