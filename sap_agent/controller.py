"""Agent loop controller (ADR D3/D4, issue #684).

Thin deterministic state machine: observe -> decide -> act -> verify -> record.
No agent framework — the loop drives typed tool steps toward a goal with a
bounded step budget, first-class stuck detection (nav loop, repeated failures,
budget/timeout), and bounded retries with full state reset for transient
failures. Every action is recorded in the session trace.

Planner mode (#693): `run_planned` lets the agent pick its own next action
from an ordered candidate table instead of following a fixed step list.
Selection stays deterministic — first candidate whose guard passes wins.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from .context import SessionContext
from .schemas import AgentResult, Config, FailureKind, PlannerDecision, StepResult, StepStatus
from .tools.auth import AuthError
from .tools.report import should_retry

logger = logging.getLogger(__name__)

Step = Callable[[Page, SessionContext], StepResult]

_DEFAULT_BUDGET = 20
_DEFAULT_STUCK_THRESHOLD = 3


@dataclass(frozen=True)
class Candidate:
    """One planner option: named step + guard over the result history (#693)."""

    name: str
    applies: Callable[[list[StepResult]], bool]
    step: Step
    rationale: str = ""


class ReasoningChain:
    """Accumulates planner decisions in order; dumped onto AgentResult."""

    def __init__(self) -> None:
        self._decisions: list[PlannerDecision] = []

    def record(self, decision: PlannerDecision) -> None:
        self._decisions.append(decision)

    def snapshot(self) -> list[PlannerDecision]:
        return list(self._decisions)


def evaluate_step_result(result: StepResult) -> bool:
    """A step counts as achieved only on SUCCESS (#693)."""
    return result.status == StepStatus.SUCCESS


class AgentLoop:
    """Runs an ordered list of typed tool steps until done or stuck."""

    def __init__(
        self,
        config: Config,
        page: Page,
        ctx: SessionContext,
        *,
        budget: int = _DEFAULT_BUDGET,
        stuck_threshold: int = _DEFAULT_STUCK_THRESHOLD,
        retry_budget: int | None = None,
    ) -> None:
        self.config = config
        self.page = page
        self.ctx = ctx
        self.budget = budget
        self.stuck_threshold = stuck_threshold
        self.retry_budget = retry_budget if retry_budget is not None else config.retry_budget
        self._nav_loop_count = 0

    def run(self, goal: str, steps: list[Step], budget: int | None = None) -> AgentResult:
        """Execute steps in order until success, budget exhaustion, or a stuck abort."""
        limit = budget or self.budget
        steps_used = 0
        index = 0
        consecutive_failures = 0
        last_key: tuple[str, str, str] | None = None
        retries_left = self.retry_budget
        outcome = None

        while index < len(steps):
            if steps_used >= limit:
                return self._finish(
                    goal,
                    limit,
                    steps_used,
                    success=False,
                    reason="step budget exhausted",
                    failure_kind=FailureKind.AGENT_LIMITATION,
                )

            step = steps[index]
            steps_used += 1
            result = self._run_step(step)
            self._record(result)
            key = (result.tool, result.action, result.outcome or result.status.value)

            if result.status == StepStatus.SKIPPED:
                index += 1
                continue

            if key == last_key:
                self._nav_loop_count += 1
            else:
                last_key = key
                self._nav_loop_count = 1
            if self._nav_loop_count >= self.stuck_threshold:
                return self._finish(
                    goal,
                    limit,
                    steps_used,
                    success=False,
                    reason=f"repeated identical action {key[0]}.{key[1]} ({key[2]})",
                    failure_kind=FailureKind.NAV_LOOP,
                )

            if result.status == StepStatus.SUCCESS:
                consecutive_failures = 0
                self._nav_loop_count = 0
                if result.payload is not None:
                    outcome = result.payload
                index += 1
                continue

            consecutive_failures += 1
            failure_kind = result.kind or FailureKind.AGENT_LIMITATION
            if consecutive_failures >= self.stuck_threshold:
                return self._finish(
                    goal,
                    limit,
                    steps_used,
                    success=False,
                    reason=f"{result.tool}.{result.action} failed {consecutive_failures} times in a row",
                    failure_kind=failure_kind,
                )

            if (result.transient or should_retry(failure_kind.value)) and retries_left > 0:
                retries_left -= 1
                logger.info(
                    "transient failure on %s.%s — state reset (%d retries left)",
                    result.tool,
                    result.action,
                    retries_left,
                )
                self._reset_state()
                continue

            return self._finish(
                goal,
                limit,
                steps_used,
                success=False,
                reason=f"step {result.tool}.{result.action} failed: {result.detail or failure_kind.value}",
                failure_kind=failure_kind,
            )

        return self._finish(goal, limit, steps_used, success=True, outcome=outcome)

    def decide_next_step(
        self,
        candidates: list[Candidate],
        history: list[StepResult],
    ) -> Candidate | None:
        """First candidate whose guard passes wins — table order is priority (#693)."""
        for candidate in candidates:
            if candidate.applies(history):
                return candidate
        return None

    def run_planned(
        self,
        goal: str,
        candidates: list[Candidate],
        *,
        goal_met: Callable[[list[StepResult]], bool],
        budget: int | None = None,
    ) -> AgentResult:
        """Planner mode (#693): pick actions from `candidates` until `goal_met`.

        Same guarantees as `run`: bounded budget, stuck abort on a repeated
        candidate or consecutive failures, transient-failure state reset.
        Every decision lands in the trace (`plan.decide.*`) and the returned
        reasoning chain.
        """
        limit = budget or self.budget
        chain = ReasoningChain()
        history: list[StepResult] = []
        steps_used = 0
        repeats = 0
        last_name: str | None = None
        consecutive_failures = 0
        retries_left = self.retry_budget
        outcome = None

        while not goal_met(history):
            if steps_used >= limit:
                return self._finish(
                    goal,
                    limit,
                    steps_used,
                    success=False,
                    reason="step budget exhausted",
                    failure_kind=FailureKind.AGENT_LIMITATION,
                    reasoning=chain.snapshot(),
                )

            candidate = self.decide_next_step(candidates, history)
            if candidate is None:
                return self._finish(
                    goal,
                    limit,
                    steps_used,
                    success=False,
                    reason="no viable action for current state",
                    failure_kind=FailureKind.AGENT_LIMITATION,
                    reasoning=chain.snapshot(),
                )

            steps_used += 1
            result = self._run_step(candidate.step)
            self._record(result)
            self.ctx.record(
                "plan",
                f"decide.{candidate.name}",
                outcome=result.outcome or result.status.value,
                url=result.url,
                detail=candidate.rationale,
            )
            chain.record(
                PlannerDecision(
                    candidate=candidate.name,
                    rationale=candidate.rationale,
                    tool=result.tool,
                    action=result.action,
                    status=result.status.value,
                )
            )

            repeats = repeats + 1 if candidate.name == last_name else 1
            last_name = candidate.name
            if repeats >= self.stuck_threshold:
                return self._finish(
                    goal,
                    limit,
                    steps_used,
                    success=False,
                    reason=f"candidate {candidate.name} repeated {repeats}x without reaching the goal",
                    failure_kind=FailureKind.NAV_LOOP,
                    reasoning=chain.snapshot(),
                )
            history.append(result)

            if evaluate_step_result(result):
                consecutive_failures = 0
                if result.payload is not None:
                    outcome = result.payload
                continue

            consecutive_failures += 1
            failure_kind = result.kind or FailureKind.AGENT_LIMITATION
            if consecutive_failures >= self.stuck_threshold:
                return self._finish(
                    goal,
                    limit,
                    steps_used,
                    success=False,
                    reason=f"{candidate.name} failed {consecutive_failures} times in a row",
                    failure_kind=failure_kind,
                    reasoning=chain.snapshot(),
                )
            if (result.transient or should_retry(failure_kind.value)) and retries_left > 0:
                retries_left -= 1
                logger.info(
                    "planner: transient failure on %s — state reset (%d retries left)",
                    candidate.name,
                    retries_left,
                )
                self._reset_state()
                continue
            return self._finish(
                goal,
                limit,
                steps_used,
                success=False,
                reason=f"candidate {candidate.name} failed: {result.detail or failure_kind.value}",
                failure_kind=failure_kind,
                reasoning=chain.snapshot(),
            )

        return self._finish(goal, limit, steps_used, success=True, outcome=outcome, reasoning=chain.snapshot())

    def _run_step(self, step: Step) -> StepResult:
        try:
            return step(self.page, self.ctx)
        except AuthError as exc:
            result = exc.result
            return StepResult(
                tool="auth",
                action="login",
                status=StepStatus.FAILURE,
                outcome=result.kind.value,
                detail=result.detail,
                url=result.landing_url,
                transient=result.transient,
            )
        except (PlaywrightError, TimeoutError) as exc:
            logger.warning("step raised %s: %s", type(exc).__name__, exc)
            return StepResult(
                tool="step",
                action="run",
                status=StepStatus.FAILURE,
                outcome="exception",
                detail=str(exc)[:300],
                transient=True,
            )

    def _record(self, result: StepResult) -> None:
        self.ctx.record(
            result.tool,
            result.action,
            result.outcome or result.status.value,
            url=result.url,
            detail=result.detail,
        )

    def _reset_state(self) -> None:
        with contextlib.suppress(PlaywrightError, TimeoutError):
            self.page.goto("about:blank", wait_until="commit")

    def _finish(
        self,
        goal: str,
        budget: int,
        steps_used: int,
        *,
        success: bool,
        reason: str = "",
        failure_kind: FailureKind | None = None,
        outcome: object | None = None,
        reasoning: list[PlannerDecision] | None = None,
    ) -> AgentResult:
        result = AgentResult(
            goal=goal,
            success=success,
            steps_used=steps_used,
            budget=budget,
            reason=reason,
            failure_kind=failure_kind,
            outcome=outcome,
            trace=self.ctx.snapshot(),
            reasoning=reasoning or [],
        )
        logger.info(
            "agent loop finished: success=%s steps=%d/%d kind=%s reason=%s",
            success,
            steps_used,
            budget,
            failure_kind.value if failure_kind else "-",
            reason,
        )
        return result
