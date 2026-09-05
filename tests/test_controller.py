"""Unit tests for the agent loop controller (issue #684).

Tools are mocked as plain functions returning StepResult — no browser, no
Playwright. Verifies the loop contracts: success, budget exhaustion, nav-loop
detection, repeated-failure abort, and transient retry with state reset.
Also covers planner mode (#693): deterministic candidate selection, stuck
abort on repeated candidates, and reasoning-chain recording.
"""

from __future__ import annotations

from fakes import PageStub

from sap_agent.context import SessionContext
from sap_agent.controller import AgentLoop, Candidate, evaluate_step_result
from sap_agent.schemas import Config, FailureKind, StepResult, StepStatus

APP_URL = "http://localhost:8080"


class FakePage(PageStub):
    def goto(self, url, *, wait_until=None, **kwargs):
        self.last_goto = url


def _config(*, retry_budget: int = 2) -> Config:
    return Config(app_url=APP_URL, username="demo", password="x", retry_budget=retry_budget)


def _ctx(config: Config | None = None) -> SessionContext:
    return SessionContext(config or _config())


def _success(tool: str = "tool", action: str = "a", outcome: str = "ok") -> StepResult:
    return StepResult(tool=tool, action=action, status=StepStatus.SUCCESS, outcome=outcome)


def _fail(
    tool: str = "tool",
    action: str = "a",
    outcome: str = "boom",
    kind: FailureKind | None = None,
    transient: bool = False,
) -> StepResult:
    return StepResult(
        tool=tool,
        action=action,
        status=StepStatus.FAILURE,
        outcome=outcome,
        detail="step failed",
        kind=kind,
        transient=transient,
    )


class TestAgentLoopSuccess:
    def test_all_steps_succeed(self) -> None:
        ctx = _ctx()
        loop = AgentLoop(_config(), FakePage(), ctx)
        calls: list[str] = []

        def step_a(_page, _ctx):
            calls.append("a")
            return _success(tool="one", action="first")

        def step_b(_page, _ctx):
            calls.append("b")
            return _success(tool="two", action="second")

        result = loop.run("goal", [step_a, step_b])
        assert result.success
        assert result.steps_used == 2
        assert result.reason == ""
        assert result.failure_kind is None
        assert calls == ["a", "b"]
        assert len(result.trace) == 2

    def test_payload_surfaced_on_result(self) -> None:
        ctx = _ctx()
        loop = AgentLoop(_config(), FakePage(), ctx)

        def step(_page, _ctx):
            return StepResult(
                tool="discover",
                action="app.summary",
                status=StepStatus.SUCCESS,
                outcome="summary",
                payload={"app_name": "Sales Dashboard"},
            )

        result = loop.run("goal", [step])
        assert result.success
        assert result.outcome == {"app_name": "Sales Dashboard"}


class TestAgentLoopBudget:
    def test_budget_exhausted_aborts(self) -> None:
        ctx = _ctx()
        loop = AgentLoop(_config(), FakePage(), ctx)

        def step(_page, _ctx):
            return _success()

        result = loop.run("goal", [step, step, step], budget=2)
        assert not result.success
        assert result.budget == 2
        assert result.steps_used == 2
        assert result.failure_kind == FailureKind.AGENT_LIMITATION
        assert "budget" in result.reason


class TestAgentLoopStuck:
    def test_nav_loop_detected(self) -> None:
        ctx = _ctx()
        loop = AgentLoop(_config(), FakePage(), ctx, stuck_threshold=3)
        calls = 0

        def step(_page, _ctx):
            nonlocal calls
            calls += 1
            return _fail(tool="nav", action="navigate", outcome="dashboard", transient=True)

        result = loop.run("goal", [step], budget=10)
        assert not result.success
        assert result.failure_kind == FailureKind.NAV_LOOP
        assert calls == 3
        assert "repeated identical action nav.navigate (dashboard)" in result.reason

    def test_nonretryable_failure_aborts_immediately(self) -> None:
        ctx = _ctx()
        loop = AgentLoop(_config(retry_budget=2), FakePage(), ctx)
        calls = 0

        def step(_page, _ctx):
            nonlocal calls
            calls += 1
            return _fail(kind=FailureKind.SELECTOR_FAILURE)

        result = loop.run("goal", [step], budget=10)
        assert not result.success
        assert result.failure_kind == FailureKind.SELECTOR_FAILURE
        assert calls == 1

    def test_repeated_failures_abort_after_threshold(self) -> None:
        ctx = _ctx()
        loop = AgentLoop(_config(retry_budget=3), FakePage(), ctx, stuck_threshold=3)
        calls = 0

        def step(_page, _ctx):
            nonlocal calls
            calls += 1
            kinds = (FailureKind.EMPTY_STATE, FailureKind.INCONSISTENT_LOAD, FailureKind.BACKEND_ERROR)
            kind = kinds[calls - 1]
            return _fail(kind=kind, transient=True, outcome=kind.value, action="load")

        result = loop.run("goal", [step], budget=10)
        assert not result.success
        assert result.failure_kind == FailureKind.BACKEND_ERROR
        assert calls == 3
        assert "failed 3 times in a row" in result.reason


class TestAgentLoopRetry:
    def test_transient_failure_retries_then_succeeds(self) -> None:
        ctx = _ctx()
        loop = AgentLoop(_config(retry_budget=2), FakePage(), ctx)
        calls = 0

        def step(_page, _ctx):
            nonlocal calls
            calls += 1
            if calls < 3:
                return _fail(kind=FailureKind.EMPTY_STATE, transient=True)
            return _success()

        result = loop.run("goal", [step], budget=10)
        assert result.success
        assert calls == 3
        assert result.steps_used == 3

    def test_retry_budget_exhausted_aborts_with_failure_kind(self) -> None:
        ctx = _ctx()
        loop = AgentLoop(_config(retry_budget=2), FakePage(), ctx, stuck_threshold=5)
        calls = 0

        def step(_page, _ctx):
            nonlocal calls
            calls += 1
            return _fail(kind=FailureKind.EMPTY_STATE, transient=True)

        result = loop.run("goal", [step], budget=10)
        assert not result.success
        assert result.failure_kind == FailureKind.EMPTY_STATE
        assert calls == 3  # initial run + 2 retries; then retry budget exhausted

    def test_skipped_step_advances_without_failing(self) -> None:
        ctx = _ctx()
        loop = AgentLoop(_config(), FakePage(), ctx)

        def skip(_page, _ctx):
            return StepResult(tool="t", action="s", status=StepStatus.SKIPPED)

        def ok(_page, _ctx):
            return _success(tool="t", action="ok")

        result = loop.run("goal", [skip, ok])
        assert result.success
        assert result.steps_used == 2


def _candidate(name: str, step, applies=None) -> Candidate:
    return Candidate(name=name, applies=applies or (lambda history: True), step=step)


def _succeeded(history: list[StepResult], tool: str) -> bool:
    return any(s.tool == tool and evaluate_step_result(s) for s in history)


class TestPlannerSelection:
    def test_picks_candidates_in_guard_order_until_goal(self) -> None:
        ctx = _ctx()
        loop = AgentLoop(_config(), FakePage(), ctx)
        calls: list[str] = []

        def login(_page, _ctx):
            calls.append("login")
            return _success(tool="auth", action="login")

        def extract(_page, _ctx):
            calls.append("extract")
            return _success(tool="extract", action="table", outcome="rows=12")

        candidates = [
            _candidate("login", login, applies=lambda h: not h),
            _candidate("extract", extract, applies=lambda h: _succeeded(h, "auth")),
        ]
        result = loop.run_planned(
            "qa",
            candidates,
            goal_met=lambda h: _succeeded(h, "extract"),
        )
        assert result.success
        assert calls == ["login", "extract"]
        assert [d.candidate for d in result.reasoning] == ["login", "extract"]
        assert any(e.tool == "plan" for e in result.trace)

    def test_no_viable_action_aborts(self) -> None:
        ctx = _ctx()
        loop = AgentLoop(_config(), FakePage(), ctx)

        candidates = [_candidate("blocked", lambda p, c: _success(), applies=lambda h: False)]
        result = loop.run_planned("qa", candidates, goal_met=lambda h: False)
        assert not result.success
        assert "no viable action" in result.reason
        assert result.failure_kind == FailureKind.AGENT_LIMITATION
        assert result.steps_used == 0

    def test_budget_exhausted_aborts_in_planner_mode(self) -> None:
        ctx = _ctx()
        loop = AgentLoop(_config(), FakePage(), ctx)
        calls = 0

        def step(_page, _ctx):
            nonlocal calls
            calls += 1
            return _success()

        result = loop.run_planned(
            "qa",
            [_candidate("loop-forever", step)],
            goal_met=lambda h: False,
            budget=2,
        )
        assert not result.success
        assert result.steps_used == 2
        assert "budget" in result.reason

    def test_repeated_candidate_aborts_as_nav_loop(self) -> None:
        ctx = _ctx()
        loop = AgentLoop(_config(), FakePage(), ctx, stuck_threshold=3)
        calls = 0

        def step(_page, _ctx):
            nonlocal calls
            calls += 1
            return _success(outcome=f"try-{calls}")

        result = loop.run_planned(
            "qa",
            [_candidate("spin", step)],
            goal_met=lambda h: False,
            budget=10,
        )
        assert not result.success
        assert result.failure_kind == FailureKind.NAV_LOOP
        assert calls == 3
        assert "repeated 3x" in result.reason

    def test_transient_failure_switches_to_alternative_candidate(self) -> None:
        ctx = _ctx()
        loop = AgentLoop(_config(retry_budget=2), FakePage(), ctx)
        calls: list[str] = []

        def flaky(_page, _ctx):
            calls.append("flaky")
            return _fail(kind=FailureKind.EMPTY_STATE, transient=True)

        def fallback(_page, _ctx):
            calls.append("fallback")
            return _success(tool="alt", action="route")

        candidates = [
            _candidate("flaky", flaky, applies=lambda h: not h),
            _candidate("fallback", fallback),
        ]
        result = loop.run_planned(
            "qa",
            candidates,
            goal_met=lambda h: _succeeded(h, "alt"),
        )
        assert result.success
        assert calls == ["flaky", "fallback"]

    def test_reasoning_chain_survives_failure_finish(self) -> None:
        ctx = _ctx()
        loop = AgentLoop(_config(), FakePage(), ctx)

        def failing(_page, _ctx):
            return _fail(kind=FailureKind.SELECTOR_FAILURE)

        result = loop.run_planned(
            "qa",
            [_candidate("broken", failing)],
            goal_met=lambda h: False,
        )
        assert not result.success
        assert len(result.reasoning) == 1
        assert result.reasoning[0].candidate == "broken"
        assert result.reasoning[0].status == StepStatus.FAILURE.value

    def test_goal_met_before_first_step_returns_success(self) -> None:
        ctx = _ctx()
        loop = AgentLoop(_config(), FakePage(), ctx)

        def never(_page, _ctx):  # pragma: no cover - must not run
            raise AssertionError("planner must not act when goal already met")

        result = loop.run_planned(
            "qa",
            [_candidate("never", never)],
            goal_met=lambda h: True,
        )
        assert result.success
        assert result.steps_used == 0
        assert result.reasoning == []


class TestEvaluateStepResult:
    def test_success_is_true_failure_is_false(self) -> None:
        assert evaluate_step_result(_success())
        assert not evaluate_step_result(_fail())
        skipped = StepResult(tool="t", action="a", status=StepStatus.SKIPPED)
        assert not evaluate_step_result(skipped)
