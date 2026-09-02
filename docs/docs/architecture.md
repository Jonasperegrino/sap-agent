# Fiori Discovery Agent — Architecture Decision Record

Status: Proposed
Owner: sap-poc
Related: #643 (umbrella), #644–#649 (implementation breakdown)

## 1. Context

Goal: an agent that can log into a Fiori app with passed credentials, autonomously
discover and summarize what the app offers, answer natural-language questions against
live Fiori / OData data, and — when stuck — retry with bounded effort, collect
reproducible evidence, and draft a bug report.

The existing `sap-poc/` provides a minimal UI5 login + dashboard app and a Playwright
test suite using SAP's `playwright-sap` (UI5-aware locators). This ADR fixes the
architecture that the sub-issues #644–#649 will implement against.

## 2. Decisions

### D1. Primary runtime: Python

Python runs the agent loop, CLI, and all non-browser logic.

- Reason: matches the repo-wide Python/uv tooling, and the agent's real complexity is
  orchestration and data handling, not browser scripting.
- Playwright is used through its Python binding (`playwright` package), driven from the
  agent loop.

### D2. Browser automation: Playwright + UI5 bridge, direct OData as supporting path

- Primary automation: Playwright-Python driving Chromium.
- Semantic access to UI5: a thin **UI5 bridge** implemented via `page.evaluate(...)`
  against the running UI5 core (`sap.ui.getCore().byId(...)`, `getElementInfo()`,
  accessibility tree, visible control introspection). This replaces reliance on
  CSS/attribute selectors and gives stable, version-tolerant reads.
- `playwright-sap` (Node) stays in `tests/` as the existing eval harness; the agent does
  not depend on it. If the UI5 bridge proves insufficient, the bridge is the single swap
  point for a `playwright-sap`-based locator strategy.
- Network capture: CDP/Playwright request events to observe OData calls backing visible
  data. Direct OData replay (`network` tool) is a *supporting* path for reproducible
  answers, never a replacement for UI-level behavior.

### D2.5 Selector strategy: semantic-first, control ids as fallback only (#652)

Selectors MUST NOT couple to UI5-generated control ids (`__xmlview{N}--{id}`; N is
never guaranteed). Strategy, in order:

1. **Semantic**: control class + role + column headers (`get_table_data` uses
   `.sapMListTbl, table[role='table']` + `th` texts). Stable across control-id renames.
2. **UI5 bridge** (`page.evaluate` against `sap.ui.getCore().byId(...)`): resolves the
   current rendered id at runtime when semantic selectors are insufficient.
3. **Hardcoded control id**: last resort, explicitly flagged as brittle.

Evidence: `tests/test_brittle_selector.py` (hardcoded `#__xmlview1--salesTable-listUl`
passes pre-rename, fails post-rename) vs `tests/test_semantic_selector.py` (green in
both states). Choreography: `scripts/rename_control.sh rename|restore`. Fix-loop
suggestion for a failed brittle selector: `suggest_semantic_selector()` (rule-based;
an LLM slot would later map failure message + DOM dump to richer suggestions).

### D3. Agent orchestration: thin deterministic controller loop, no framework

No LangGraph or general-purpose agent framework. A small state machine:

```
observe -> decide -> act -> verify -> record
```

- `controller.py` owns the loop and a bounded step/retry budget.
- Each loop iteration picks one internal tool, runs it, checks the result against the
  current goal, and records a trace entry.
- Stuck detection is a first-class hook in the loop (see D4, #648).

### D4. Internal tool boundaries

Seven tools, each a module with a narrow contract:

| Tool | Responsibility | Issue |
| --- | --- | --- |
| `auth` | credential intake, login flow, session state, auth failure taxonomy, retry policy | #645 |
| `nav` | shell/route/dialog navigation, back/refresh, wait-for-stable | #646 |
| `extract` | UI5 bridge reads: tables, filters, forms, counters, labels, visible actions | #646 |
| `network` | OData/service capture, direct OData queries for reproducible answers | #646/#647 |
| `reason` | LLM: intent→plan mapping, summary synthesis, answer generation w/ confidence | #646/#647 |
| `report` | artifact collection (screenshots, traces), bug-report drafting | #648 |
| `context` | session context: goal, history, trace, secrets handling rules | all |

Contract rule: tools return typed structures (D5), never free-form text, so the
controller can verify outcomes deterministically.

### D5. Artifact formats

All artifacts are JSON (plus screenshots), versioned by schema key.

- `AppSummary` — areas, entities, filters, tables (with columns + row counts), actions,
  detected OData services, likely business domain, ranked surfaces.
- `AnswerPayload` — answer, evidence (screen/filter/table/endpoint/row source),
  confidence, trace reference, follow-up prompt when ambiguous.
- `Trace` (JSONL) — ordered actions: tool, input, outcome, timestamps, URLs.
- `BugReport` — template (markdown): expected vs actual, repro steps, environment,
  artifacts list; must be free of secrets (see #645).
- Screenshots (PNG) captured at decision points and on failure.

### D6. Exposure: standalone CLI first, MCP later

- MVP ships as a CLI: `fiori-agent ask --app <url> --question "..."` and
  `fiori-agent discover --app <url>`.
- MCP server exposure is a follow-up wrapper around the same controller — not a reason
  to change the core architecture.

### D7. Module layout (Python package)

```
sap_agent/
  cli.py            # entry points: login / inspect / discover / ask-status / ask / report / qa / agent
  controller.py     # agent loop: budgets, stuck detection, planner mode (run_planned)
  context.py        # session context, goal, trace
  schemas.py        # pydantic: Config (env-driven), AppSummary, AnswerPayload, QaReport, BugReport, ...
  memory.py         # QA report history + cross-run diff (new/persistent/resolved)
  ui5/
    bridge.py       # page.evaluate helpers against UI5 core
  tools/            # primitives + workflows over them
    auth.py  nav.py  extract.py  network.py  reason.py  answer.py  report.py
    discover.py  qa.py  accessibility.py  ux_critique.py  screenshot.py  severity.py
  ui/
    terminal.py     # colored demo output (header/steps/issues/summary)
evals/              # scenario harness: run_eval.py + scenarios.json (`make eval`)
app/                # self-contained UI5 PoC app (vendored runtime via `make vendor-ui5`)
tests/              # unit + integration against the PoC app
```

Note: the config lives in `schemas.py` (`Config.from_env`), not a separate
`config.py`; selector strategy is embedded in the extract/nav/discover tools
(semantic-first per D2.5) rather than a standalone `selectors.py`.

## 3. System flow

```
login -> discovery -> question answering -> stuck handling
```

1. **Auth** (#645): read credentials (env/secret prompt, never log them), login, classify
   failure (bad creds / SSO boundary / timeout / element missing / redirect loop), retry
   within budget.
2. **Discovery** (#646): walk shell/routes, extract widgets via UI5 bridge, capture
   network for OData services, rank surfaces → `AppSummary`.
3. **Q&A** (#647): map intent → plan (UI navigation and/or direct OData), execute, verify
   evidence, emit `AnswerPayload`.
4. **Stuck handling** (#648): on repeated nav loops, selector failures, inconsistent
   loads, auth bounces, backend errors → bounded retry with state reset; if still stuck,
   collect artifacts and draft `BugReport`, classifying product bug vs agent limitation vs
   unsupported auth flow.

## 3.5 Auth foundation specifics (implemented, #645)

- **Login contract**: credentials from env (`SAP_AGENT_USER`, `SAP_AGENT_PASSWORD`,
  `SAP_AGENT_URL`) or interactive secure prompt (`getpass`); never from argv.
- **Session**: Playwright browser context per run; cookies live only for the run.
  Session reuse detected by landing directly on `success_route` without a login form.
- **Failure taxonomy**: `AuthFailureKind` — bad_credentials, timeout, element_not_found,
  redirect_loop, sso_unsupported, network_error. `AuthResult` carries kind + sanitized
  detail.
- **Retry policy**: transient kinds (timeout, element_not_found, network) retry up to
  `retry_budget` with full page reset (`about:blank`); deterministic kinds
  (bad_credentials, redirect_loop, sso_unsupported) fail fast without retry.
- **Security constraints**: password is a pydantic `SecretStr`; never serialized into
  `AuthResult`, trace, logs, or screenshots; bad-credentials toast text is the only
  credential-derived signal used.

## 3.6 Discovery algorithm (implemented, #646)

`discover_app()` combines DOM and network signals, deterministic first:

1. **Identity**: page title from semantic header selectors (`.sapMIBar-title`, `.sapMTitle`, `h1`), falls back to `document.title`.
2. **Data-bearing widgets**: `get_all_tables()` — every visible UI5 table on the page via
   `TABLE_ROLE_SELECTOR` (semantic, no control ids), each with columns + row count.
3. **Network signals**: `NetworkCapture` same-origin response URLs → endpoints; JSON list
   bodies are parsed and their primary-key values are matched against rendered first
   column cells (**data overlap**) to link each table to the endpoint that feeds it —
   no name guessing.
4. **Controls**: filters (`sapMInputBase`, `sapMSelect`, `sapMComboBox`), forms
   (`sapUiForm`), actions (visible `sapMBtn`) counted by semantic selectors.
5. **Entities**: one per endpoint stem (`sales.json` → `sales`), linked to their tables.
6. **Domain**: keyword heuristic over title + column headers (sales/order, customer,
   inventory, finance, hr); `unknown` when nothing matches.
7. **Ranking**: surfaces sorted by row count (primary data-bearing widgets first) into
   `ranked_surfaces`.

LLM summarization is NOT part of M1; the structured `AppSummary` is fully deterministic
and therefore testable. On a real Fiori target the same pipeline runs — the only diff is
navigation: the agent walks the shell/routes first so `get_all_tables` sees every screen.

## 3.7 Question answering (implemented, #647)

**Supported question classes** (rule-based intent parser, `tools/reason.py`):

| Intent | Example | Behavior |
| --- | --- | --- |
| `COUNT_TOTAL` | "how many orders are there?" | total rendered rows |
| `COUNT_WHERE` | "how many orders were built in 2026?" | rows matching column=value (comparer `year` → `value[:4] == cell[:4]`) |
| `EXISTENCE` | "is there any shipped order?" | 1 if ≥1 match, else not_found |
| `LOOKUP` | "find the order for Acme Corp" | parsed, needs follow-up value |
| `UNSUPPORTED` | any other question | explicit `unsupported` + message |

**Intent mapping** is deterministic: pattern match → column keyword (`status/customer/amount/built`;
`order` is deliberately NOT a column keyword) → value extraction from the original question
case. Year values imply the `built` column.

**Answer payload** (`AnsweredQuestion`): `question`, `intent`, `answer`, `not_found`,
`unsupported`, `message`, `evidence {source, column, matched_rows, endpoint}`,
`confidence`, `follow_up`, `checksum`. Checksum = sha256 over stable fields —
repeated runs must reproduce byte-identical payloads.

**Example catalog flow (AC3)** — "how many orders were built in 2026":
1. parse → `COUNT_WHERE`, column `built`, value `2026`, comparer `year`
2. snapshot rendered table, resolve column case-insensitively → `Built`
3. match `cell[:4] == "2026"` → 3 rows (SO-1001, SO-1002, SO-1004)
4. evidence `{column: Built, matched_rows: 3}`, confidence high, checksum set
   → live answer `3`, exit 0

**Defined edge behavior**:
- empty result → `not_found: true`, answer `null`, `matched_rows: 0` (never invented `0`)
- unknown column → `unsupported: true` + `follow_up` listing available columns
- ambiguous question (no value) → `unsupported` + `follow_up` "which value?"
- determinism: same question 3× → identical payload + checksum (tested)

## 3.8 Stuck handling + bug reporting (implemented, #648)

**Failure classification matrix** (`report.classify_failure`):

| Failure kind | Blame class | Retry |
| --- | --- | --- |
| `nav_loop` | product_bug | no (state persists) |
| `inconsistent_load` | product_bug | yes (bounded, state reset) |
| `empty_state` | product_bug | yes (bounded) |
| `backend_error` | product_bug | yes (bounded) |
| `selector_failure` | agent_limitation | no (would repeat) |
| `agent_limitation` | agent_limitation | no |
| auth `redirect_loop` | product_bug | no |
| auth `timeout` / `network_error` / `element_not_found` | agent_limitation | yes (existing #645 policy) |
| auth `bad_credentials` / `sso_unsupported` | unsupported_auth_flow | no (fail fast) |

**Retry budget / stop conditions**: `config.retry_budget` (default 3) bounds all
transient retries; deterministic kinds fail fast (budget never consumed). Stop when:
retries exhausted, kind not in the retryable set, or a nav loop collapses to a
previously visited route.

**Artifacts collected on failure** (`collect_artifacts`): current URL, visible table
summary, last 10 trace entries, screenshot (PNG), UTC timestamp. Credentials (username
+ password) are listed as secret values and rendered as `[REDACTED]` in the markdown —
trace and env never carry them by construction.

**Bug report template**: `BugReport.to_markdown()` — title, classification, expected vs
actual, reproduction steps, environment, attachments list, trace tail. Dropped into
`artifacts/bug_report.md`; `fiori-agent report` exits 1 on failure (report written), 0
on success (nothing to report).

## 3.9 Multipage PoC extension (implemented)

The PoC app is now multipage (dashboard / catalog / order history / customer
detail) with three served data endpoints (`sales.json`, `customers.json`,
`products.json`) and client-side ordering via `localStorage`:

- **`tools/nav.py`** closes the ADR D4 gap: menu-driven `navigate(route)`,
  `open_first_row()` (table row → detail drill-down), `go_back()` — semantic,
  never control-id bound (D2.5).
- **Page-aware answering**: `evaluate_question(..., route=...)` navigates before
  snapshotting, so the same question can be answered per page (dashboard = all
  orders, history = 2026 filter).
- **Multi-area discovery**: `discover_app` walks the menu (dashboard → catalog →
  order history) plus one customer-detail drill-down; `areas` aggregates visited
  routes; `customers.json` is a legitimately table-less entity — it is captured
  via endpoints, not linked tables.

Non-goals preserved: answers remain deterministic (no LLM in the answer path),
created orders live only in the browser (localStorage), discovery stays on the
UI surface.

## 3.10 QA workflow (implemented, #685)

`fiori-agent qa` logs in once, walks the route graph (dashboard → catalog →
orders → customer detail via `open_first_row`), and audits every page:

- **Screenshots** (`tools/screenshot.py`, #686): full-page capture per route, PNG
  written to the session artifact dir; screenshot metadata recorded in the report.
- **Accessibility audit** (`tools/accessibility.py`, #687): deterministic in-browser
  checks (same `page.evaluate` pattern as the UI5 bridge) — missing/empty `alt`,
  controls without accessible names, heading-order violations, form-label
  association, and text/background contrast ratios. `Severity` per issue
  (high/medium/low).
- **UX critique** (`tools/ux_critique.py`, #688): visual heuristics — title size,
  touch-target size, element overlap, low text contrast — scoped to the visible
  `.sapMPage` so stale hidden views cannot double-report.
- **Performance hints** (`tools/qa.py`): resource-entry stats via
  `performance.getEntriesByType('resource')` (request count, >500 KB payloads,
  >3 s durations).
- **Cross-page consistency** (`_consistency_check`): page-title font sizes compared
  across routes; routes deviating >25 % from the median get a `page_consistency`
  finding.

Output (`QaReport`, `schemas.py`): one `QaPageReport` per route with screenshots +
issues + hints, plus a roll-up (`total_issues`, `counts_by_severity`). Persisted as
`artifacts/qa_report.json`; `cli qa` prints the summary and exits 0. Deterministic —
no LLM in any check, so the whole pass is regression-testable.

## 4. First implementation target (M1) — against sap-poc

Scope of the first vertical slice, executed via #644 → #645 → #646 → #647 → #648:

1. **PoC hardening**: dashboard data moves from hardcoded controller JSON to a served
   JSON/CSV fetched over HTTP (nginx static file), so the agent has a real network call to
   capture and count. Demo credentials stay `demo` / `password123`.
2. **Agent skeleton**: `sap_agent` package with CLI, controller, UI5 bridge, and the
   `auth`, `nav`, `extract`, `network` tools.
3. **M1 scenario**: `fiori-agent ask --app http://localhost:8080 --user demo --password … \
   --question "how many sales orders are approved?"` returns:
   - answer `2` (matching the fixture data),
   - evidence: dashboard table + status column + row source,
   - trace JSONL, no secrets in any artifact.
4. **Failure path**: wrong credentials → classified auth failure, clean diagnostics,
   no password in logs/screenshots/traces.
5. **Discovery**: `fiori-agent discover` produces an `AppSummary` naming the dashboard,
   sales table, its columns and row count, and the served data endpoint.

Success criteria (all must pass):
- M1 ask scenario answers correctly and deterministically.
- M1 failure path classifies and reports without leaking credentials.
- `AppSummary` extraction works on the PoC.
- Eval harness (#649) runs the M1 scenarios green on a fresh `docker compose up`.

Non-goals for M1: real-Fiori access, MCP server, SSO/MFA paths, multi-session memory.

## 5. Open questions for later milestones

- Real Fiori target selection + access (blocked on credentials/environment) — #649.
- Whether the UI5 bridge needs `playwright-sap` semantics for exotic controls.
- HAR vs CDP-event storage format for long traces.
