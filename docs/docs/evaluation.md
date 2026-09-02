# Evaluation Harness

Repeatable, deterministic evaluation of the SAP Fiori agent against the local
PoC before any real-Fiori trial.

## Running

```sh
make eval                            # = uv run python evals/run_eval.py
```

Requires the PoC app on :8080 (`make serve`) and Chromium. Exit code 0 = all
scenarios pass (CI-safe). Each scenario is a real CLI subprocess
(`uv run python -m sap_agent.cli <cmd>`); stdout JSON is scored against the
golden in `evals/scenarios.json`.

Every run is persisted to `artifacts/eval_runs/<timestamp>.json` (per-scenario
verdicts, metrics, git SHA, app URL) and appended to
`artifacts/eval_runs/history.md` — a pass-rate-over-time trend table. Set
`SAP_AGENT_VERSION` to override the recorded version string.

## Scenario matrix (local PoC — 19 scenarios, 6 kinds)

| # | id | kind | dimension | input | golden |
| --- | --- | --- | --- | --- | --- |
| 1 | `login-good` | login | auth | demo / password123 | exit 0 |
| 2 | `login-bad-password` | login | auth (negative) | wrong password | exit 1 |
| 3 | `discover-summary` | discover | discovery | — | entity `sales`, columns ⊇ {Customer, Amount, Status, Built} |
| 4 | `discover-multiarea` | discover | discovery | — | entities {sales, products, customers}, tables ≥ 4 |
| 5 | `inspect-dashboard` | inspect | discovery | — | exit 0 |
| 6 | `ask-status-approved` | ask | Q&A count | "how many orders are Approved" | answer `4` |
| 7 | `ask-built-2026` | ask | Q&A count | "how many orders were built in 2026" | answer `10` |
| 8 | `ask-status-cancelled` | ask | Q&A count | "how many orders are Cancelled" | answer `1` |
| 9 | `ask-status-rejected` | ask | Q&A count | "how many orders are Rejected" | answer `1` |
| 10 | `ask-not-found` | ask | Q&A (negative) | "how many orders are Returned" | `not_found: true` |
| 11 | `ask-existence-customer-notfound` | ask | Q&A (negative) | "how many orders are from Nonexistent Corp" | `not_found: true` |
| 12 | `ask-unsupported` | ask | Q&A (negative) | "which customer spent the most" | `unsupported: true` |
| 13 | `ask-products-total` | ask | Q&A cross-page | catalog route: "how many products are there" | answer `15` |
| 14 | `ask-products-category` | ask | Q&A cross-page | catalog route: Machinery products | answer `4` |
| 15 | `ask-orders-2026-history` | ask | Q&A cross-page | orders route: "how many orders are there" | answer `10` |
| 16 | `ask-customer-orders` | ask | Q&A filter | "how many orders for Acme Corp" | answer `2` |
| 17 | `report-bad-creds` | report | bug reporting | wrong password | exit 1, all markdown sections present, no secret leak |
| 18 | `qa-full-audit` | qa | audit | — | routes {dashboard, catalog, orders, customer}, pages ≥ 4, md sections |
| 19 | `qa-issue-scoring` | qa | audit | — | a11y issues ≥ 5, UX issues ≥ 8, severities {medium, low} |

Goldens are locked to what the **UI displays**, not raw fixtures: the products
file has 17 rows but the Catalog shows 15 (filters inactive + zero-stock), and
Order History paginates to the 10 orders built in 2026. If the fixture or
display rules change, update the goldens — the harness is the contract.

## Success metrics

Per scenario (printed in verdict table, stored in `ScenarioResult.metrics`,
persisted per run):

- **verdict** — pass/fail against golden
- **answer_correct** — golden answer/intent/flag fields matched (ask scenarios)
- **retries** — consumed auth attempts beyond the first (parsed from stderr)
- **time_to_answer_ms** — wall-clock duration of the scenario run
- **report_completeness** — bug-report markdown contains all expected sections
  and leaks no credentials (report scenarios)

Harness-level: `N/M scenarios passed`; anything < M exits nonzero.

## Acceptance test plan (PoC)

After every agent change, gate on:

1. `make test` — unit + integration suite green (currently 193 tests),
   coverage gate at 80%.
2. `uv run ruff check .` — clean.
3. `make eval` — all 19 scenarios pass; check `history.md` trend stays flat.
4. No credential ever appears in trace, stdout JSON, or drafted bug reports
   (covered by tests + `report-bad-creds` scenario).

## Path to real-Fiori validation

1. Keep scenario format unchanged; only the target changes.
2. New scenarios arrive as access details land:
   - real login (may be MFA/SSO → `sso_unsupported` classification expected)
   - real entity discovery (goldens from curated app screenshots/manifests)
   - real question answering (goldens from domain expert review)
   - production incident reproduction (goldens from defect tickets)
3. `scenarios.json` gains an `environments` list (`local-poc`, `real-fiori`);
   the harness runs the union, scoring each environment separately.
4. Metric budgets (AC): answer_correct 100% on goldens; retries ≤ budget
   (config default 3); time_to_answer bounded per environment profile;
   bug-report completeness 100% (all sections, secret-free).
5. First real-Fiori run: same CLI commands, manually reviewed verdicts, then
   promote to CI once flake rate < 5% across 10 consecutive runs.
