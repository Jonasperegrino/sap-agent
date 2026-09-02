"""Typed artifacts shared across the agent. No secrets may appear in these structures."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, SecretStr


class AuthFailureKind(StrEnum):
    """Taxonomy of authentication failures (issue #645)."""

    BAD_CREDENTIALS = "bad_credentials"
    TIMEOUT = "timeout"
    ELEMENT_NOT_FOUND = "element_not_found"
    REDIRECT_LOOP = "redirect_loop"
    SSO_UNSUPPORTED = "sso_unsupported"
    NETWORK_ERROR = "network_error"


TRANSIENT_FAILURES = frozenset(
    {
        AuthFailureKind.TIMEOUT,
        AuthFailureKind.ELEMENT_NOT_FOUND,
        AuthFailureKind.NETWORK_ERROR,
    }
)


class AuthResult(BaseModel):
    """Outcome of a login attempt. `detail` is always sanitized (no secrets)."""

    ok: bool
    kind: AuthFailureKind | None = None
    landing_url: str | None = None
    detail: str = ""
    attempts: int = Field(ge=1)
    #: by route, e.g. `#/dashboard` — used as post-login verification target
    verified_route: str | None = None

    @property
    def transient(self) -> bool:
        return self.kind in TRANSIENT_FAILURES

    def sanitized(self) -> dict[str, Any]:
        """Serializable form guaranteed free of credentials."""
        return self.model_dump()


class TraceEntry(BaseModel):
    """One recorded agent action (trace stream, JSONL-friendly)."""

    tool: str
    action: str
    outcome: str
    url: str | None = None
    detail: str = ""


class QuestionIntent(StrEnum):
    """Supported MVP question classes (#647)."""

    COUNT_TOTAL = "count_total"  # "how many orders are there?"
    COUNT_WHERE = "count_where"  # "how many orders were built in 2026?"
    EXISTENCE = "existence"  # "is there any approved order?"
    LOOKUP = "lookup"  # "find the order for Acme Corp"
    AGGREGATE = "aggregate"  # "revenue of top 3 clients last year" -> sum/group/sort/limit
    UNSUPPORTED = "unsupported"


class IntentConfig(BaseModel):
    """Parsed intent: what to count, filter on, and how to compare."""

    intent: QuestionIntent = QuestionIntent.UNSUPPORTED
    column: str | None = None
    value: str | None = None
    comparer: str = "exact"  # exact | year
    follow_up: str = ""
    # aggregate extensions (LLM slot #647): sum/avg/count grouped + ranked
    aggregation: str | None = None  # sum | avg | count
    aggregation_column: str | None = None  # e.g. amount
    group_by: str | None = None  # e.g. customer
    limit: int | None = None
    sort_order: str = "desc"


class AnswerEvidence(BaseModel):
    """Where an answer came from — reproducible source context (#647)."""

    source: str = ""
    column: str = ""
    matched_rows: int = 0
    endpoint: str | None = None


class AnsweredQuestion(BaseModel):
    """Deterministic answer with evidence. No invented data, ever."""

    question: str
    intent: QuestionIntent = QuestionIntent.UNSUPPORTED
    answer: int | list[dict[str, Any]] | None = None
    not_found: bool = False
    unsupported: bool = False
    message: str = ""
    evidence: AnswerEvidence = AnswerEvidence()
    confidence: str = "none"
    follow_up: str = ""
    checksum: str = ""


class DiscoveredTable(BaseModel):
    """A data-bearing table found during discovery (#646)."""

    name: str = ""
    columns: list[str] = Field(default_factory=list)
    row_count: int = 0
    endpoint: str | None = None


class DiscoveredEntity(BaseModel):
    """Business entity grouped by endpoint stem, e.g. sales <- sales.json."""

    name: str = ""
    tables: list[DiscoveredTable] = Field(default_factory=list)
    endpoints: list[str] = Field(default_factory=list)


class AppSummary(BaseModel):
    """Structured app map: areas, entities, filters, tables, actions, domain."""

    app_name: str = ""
    areas: list[str] = Field(default_factory=list)
    entities: list[DiscoveredEntity] = Field(default_factory=list)
    tables: list[DiscoveredTable] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    forms: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    domain: str = ""
    ranked_surfaces: list[str] = Field(default_factory=list)


class FailureKind(StrEnum):
    """Agent failure taxonomy beyond auth (issue #648)."""

    NAV_LOOP = "nav_loop"
    SELECTOR_FAILURE = "selector_failure"
    INCONSISTENT_LOAD = "inconsistent_load"
    EMPTY_STATE = "empty_state"
    BACKEND_ERROR = "backend_error"
    AGENT_LIMITATION = "agent_limitation"


class FailureClass(StrEnum):
    """Who to blame: the product, the agent, or an unsupported auth flow."""

    PRODUCT_BUG = "product_bug"
    AGENT_LIMITATION = "agent_limitation"
    UNSUPPORTED_AUTH_FLOW = "unsupported_auth_flow"


class BugReport(BaseModel):
    """Reproducible bug-report draft (issue #648). Secret-free by construction."""

    title: str = ""
    expected: str = ""
    actual: str = ""
    classification: FailureClass = FailureClass.AGENT_LIMITATION
    reproduction_steps: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)
    trace_tail: list[str] = Field(default_factory=list)
    #: values scrubbed as [REDACTED] in rendered markdown (never empty by construction)
    secret_values: list[str] = Field(default_factory=list)

    def _scrub(self, text: str) -> str:
        for secret in self.secret_values:
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text

    def to_markdown(self) -> str:
        title = self._scrub(self.title)
        expected = self._scrub(self.expected)
        actual = self._scrub(self.actual)
        lines = [
            f"# {title}",
            "",
            f"**Classification**: `{self.classification.value}`",
            "",
            "## Expected",
            expected,
            "",
            "## Actual",
            actual,
            "",
            "## Reproduction steps",
        ]
        steps = [self._scrub(s) for s in self.reproduction_steps]
        lines += [f"{i}. {step}" for i, step in enumerate(steps, 1)]
        lines += [
            "",
            "## Environment",
            "".join(f"- {k}: {self._scrub(v)}" for k, v in sorted(self.environment.items())),
            "",
            "## Attachments",
        ]
        lines += [f"- `{a}`" for a in self.artifacts] or ["- none"]
        lines += [
            "",
            "## Last agent actions (trace tail)",
        ]
        tail = [self._scrub(t) for t in self.trace_tail]
        lines += [f"- `{t}`" for t in tail] or ["- none"]
        return "\n".join(lines) + "\n"


class StepStatus(StrEnum):
    """Outcome of a single agent-loop step (issue #684)."""

    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"


class StepResult(BaseModel):
    """Typed result of one loop step — controller verifies these deterministically."""

    tool: str
    action: str
    status: StepStatus = StepStatus.SUCCESS
    outcome: str = ""  # short machine-readable outcome string (nav-loop key)
    detail: str = ""
    url: str | None = None
    #: optional agent-failure classification (selector_failure, empty_state, ...)
    kind: FailureKind | None = None
    #: true when the failure is transient and a state-reset retry may help
    transient: bool = False
    #: optional structured payload (e.g. AppSummary) surfaced on AgentResult
    payload: Any = None


class PlannerDecision(BaseModel):
    """One planner decision: chosen candidate + rationale (#693)."""

    candidate: str
    rationale: str = ""
    tool: str = ""
    action: str = ""
    status: str = ""


class AgentResult(BaseModel):
    """Emitted when the loop stops: success/failure + evidence (issue #684).

    `outcome` carries the last step's structured payload (e.g. AppSummary) so
    callers can consume the result without re-reading the browser. `trace` is a
    serializable copy of the session trace — secret-free by contract.
    """

    goal: str
    success: bool
    steps_used: int
    budget: int
    reason: str = ""
    failure_kind: FailureKind | None = None
    outcome: Any = None
    trace: list[TraceEntry] = Field(default_factory=list)
    #: planner mode only (#693): decided steps + rationale, in order
    reasoning: list[PlannerDecision] = Field(default_factory=list)


class Severity(StrEnum):
    """Issue severity for QA reports (issue #698 rules)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AccessibilityIssue(BaseModel):
    """One accessibility finding: type, element, severity, suggestion."""

    type: str
    element: str
    severity: Severity = Severity.LOW
    suggestion: str = ""


class UxIssue(BaseModel):
    """One visual/UX finding: type, element, severity, suggestion, screenshot."""

    type: str
    element: str = ""
    severity: Severity = Severity.LOW
    suggestion: str = ""
    screenshot: str = ""


class ScreenshotResult(BaseModel):
    """Metadata for a captured screenshot (issue #686)."""

    route: str
    path: str
    width: int = 0
    height: int = 0
    captured_at: str = ""
    element: str = ""


class QaPageReport(BaseModel):
    """Per-page QA findings: screenshots, accessibility + UX issues, perf hints."""

    route: str
    screenshots: list[ScreenshotResult] = Field(default_factory=list)
    accessibility_issues: list[AccessibilityIssue] = Field(default_factory=list)
    ux_issues: list[UxIssue] = Field(default_factory=list)
    performance_hints: list[str] = Field(default_factory=list)


class QaReport(BaseModel):
    """Full QA run: every audited page + roll-up summary (issue #685)."""

    app_url: str
    generated_at: str = ""
    pages: list[QaPageReport] = Field(default_factory=list)

    @property
    def total_issues(self) -> int:
        return sum(len(p.accessibility_issues) + len(p.ux_issues) for p in self.pages)

    def counts_by_severity(self) -> dict[str, int]:
        counts = {s.value: 0 for s in Severity}
        for page in self.pages:
            for issue in [*page.accessibility_issues, *page.ux_issues]:
                counts[issue.severity.value] += 1
        return counts

    def to_markdown(self) -> str:
        """Human-readable report (#674); mirrors the JSON artifact."""
        counts = self.counts_by_severity()
        lines = [
            f"# QA Report — {self.app_url}",
            "",
            f"Generated: {self.generated_at or '-'}",
            "",
            (
                f"**Pages**: {len(self.pages)} · **Issues**: {self.total_issues} "
                f"(high {counts[Severity.HIGH.value]}, medium {counts[Severity.MEDIUM.value]}, "
                f"low {counts[Severity.LOW.value]})"
            ),
            "",
        ]
        for page in self.pages:
            lines += [f"## {page.route}", ""]
            for shot in page.screenshots:
                lines.append(f"- screenshot: `{shot.path}` ({shot.width}x{shot.height})")
            for issue in page.accessibility_issues:
                lines.append(f"- [a11y/{issue.severity.value}] {issue.type}: {issue.element} — {issue.suggestion}")
            for issue in page.ux_issues:
                lines.append(f"- [ux/{issue.severity.value}] {issue.type}: {issue.element} — {issue.suggestion}")
            for hint in page.performance_hints:
                lines.append(f"- [perf] {hint}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


class DiffIssue(BaseModel):
    """One issue placed across two QA runs (#694)."""

    route: str
    source: str  # "a11y" | "ux"
    type: str
    element: str = ""
    severity: Severity = Severity.LOW
    #: new | persistent | resolved
    status: str = "new"


class DiffReport(BaseModel):
    """Cross-run comparison of two QaReports (#694)."""

    old_generated_at: str = ""
    new_generated_at: str = ""
    issues: list[DiffIssue] = Field(default_factory=list)

    def counts_by_status(self) -> dict[str, int]:
        counts = {"new": 0, "persistent": 0, "resolved": 0}
        for issue in self.issues:
            counts[issue.status] = counts.get(issue.status, 0) + 1
        return counts


class Config(BaseModel):
    """Agent configuration driven by env vars (prefix SAP_AGENT_)."""

    app_url: str = Field(default="http://localhost:8080")
    username: str = ""
    password: SecretStr = SecretStr("")
    login_timeout_ms: int = Field(default=30_000, ge=1_000)
    retry_budget: int = Field(default=3, ge=1, le=10)
    #: base delay for exponential backoff between login retries (#680), capped at 5s
    retry_backoff_s: float = Field(default=0.5, ge=0)
    #: navigation wait window (#676)
    nav_timeout_ms: int = Field(default=10_000, ge=1_000)
    #: table extraction wait window (#676)
    extract_timeout_ms: int = Field(default=15_000, ge=1_000)
    headless: bool = True
    artifacts_dir: str = "artifacts"
    log_level: str = "INFO"
    #: post-login route that proves authentication succeeded, e.g. "#/dashboard"
    success_route: str | None = None
    #: LLM slot (issue #647 extension): OpenAI-compatible API for intent parsing
    llm_api_key: SecretStr | None = None
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_provider: str = "openai"  # openai | anthropic | openai-compatible
    llm_timeout_s: float = Field(default=15.0, ge=1.0)

    @classmethod
    def from_env(cls, **overrides: Any) -> Config:
        import os

        env: dict[str, Any] = {
            "app_url": os.environ.get("SAP_AGENT_URL", "http://localhost:8080"),
            "username": os.environ.get("SAP_AGENT_USER", ""),
            "password": SecretStr(os.environ.get("SAP_AGENT_PASSWORD", "")),
            "log_level": os.environ.get("SAP_AGENT_LOG_LEVEL", "INFO"),
        }
        raw_backoff = os.environ.get("SAP_AGENT_RETRY_BACKOFF_S")
        if raw_backoff:
            env["retry_backoff_s"] = float(raw_backoff)
        raw_nav_timeout = os.environ.get("SAP_AGENT_NAV_TIMEOUT_MS")
        if raw_nav_timeout:
            env["nav_timeout_ms"] = int(raw_nav_timeout)
        raw_extract_timeout = os.environ.get("SAP_AGENT_EXTRACT_TIMEOUT_MS")
        if raw_extract_timeout:
            env["extract_timeout_ms"] = int(raw_extract_timeout)
        # LLM env (optional, no hard dependency)
        raw_llm_key = os.environ.get("SAP_AGENT_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if raw_llm_key:
            env["llm_api_key"] = SecretStr(raw_llm_key)
        raw_llm_model = os.environ.get("SAP_AGENT_LLM_MODEL") or os.environ.get("OPENAI_MODEL")
        if raw_llm_model:
            env["llm_model"] = raw_llm_model
        raw_llm_base = os.environ.get("SAP_AGENT_LLM_BASE_URL")
        if raw_llm_base:
            env["llm_base_url"] = raw_llm_base.rstrip("/")
        raw_llm_provider = os.environ.get("SAP_AGENT_LLM_PROVIDER")
        if raw_llm_provider:
            env["llm_provider"] = raw_llm_provider
        raw_llm_timeout = os.environ.get("SAP_AGENT_LLM_TIMEOUT_S")
        if raw_llm_timeout:
            env["llm_timeout_s"] = float(raw_llm_timeout)
        env.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**env)

    def has_credentials(self) -> bool:
        return bool(self.username and self.password.get_secret_value())

    def has_llm(self) -> bool:
        return bool(self.llm_api_key and self.llm_api_key.get_secret_value())
