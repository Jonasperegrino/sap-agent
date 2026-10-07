# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary: QA / operator running discovery, Q&A, and audits against a SAP Fiori / UI5 app. Situation: local run or Streamlit Cloud operator UI against live demo app. Job: verify app behavior, answer count/existence questions with evidence, sweep every route for a11y/UX issues, file reproducible bug reports.

## Product Purpose

Autonomous agent over a SAP Fiori / UI5 app: discovers pages + data, answers questions with typed evidence, audits accessibility/UX across every route, files reproducible bug reports. Deterministic-by-default; LLM is optional fallback. Success: correct answers with evidence + checksum, full-route audits with screenshots, no invented data.

## Positioning

Open decision: user requested rewrite of deterministic-evidence positioning, no replacement confirmed yet. Working hypothesis (do not treat as confirmed): deterministic rules-first discovery/Q&A/audit with typed evidence and checksums over live UI5, LLM only as fallback.

## Operating Context

CLI workflows (`login`, `inspect`, `discover`, `ask`, `report`, `qa`, `agent`, `story`) plus Streamlit operator UI (`Ask Atlas` / `Bug reports` tabs; `Investigate` behind `SAP_AGENT_SHOW_INVESTIGATE_TAB`). Target: live demo `https://jonasperegrino.github.io/sap-fiori/` (repo `../sap-fiori`, GitHub Pages); local fallback via `FIORI_APP_DIR` / `SAP_AGENT_URL=http://localhost:8080`. Config via `SAP_AGENT_*` env (URL, USER, PASSWORD, LLM_* timeouts); Streamlit Cloud secrets mirror env. Quality gates: `make test` (pytest + 80% coverage), `make lint` (ruff), `make eval` (19 deterministic scenarios → `artifacts/eval_runs/`).

## Capabilities and Constraints

Confirmed: Playwright Chromium discovery of UI5 pages/entities/tables; `AppSummary` JSON; count/existence Q&A with confidence + checksum; full audit walk (screenshots, a11y, UX, perf hints); deterministic `story` artifacts (uc1/uc2/uc3); bug-report draft on login failure. Constraints: engine must never take UI down (guarded import + degraded mode); credentials via env/secure prompt never argv; CSS + Streamlit builtins only for operator UI; Chromium warmup non-blocking.

## Brand Commitments

Name: Atlas for SAP (page title "Atlas for SAP"). Voice: precise audit tool, enterprise calm. Colors are free (previous `#0a6ed1` single-accent constraint lifted per user). Modern font allowed. No binding palette/type committed.

## Evidence on Hand

Real: live Fiori demo app above; repo docs (`README.md`, `docs/architecture.md`, `ui_plan.md` with as-built notes 2026-10-04); deterministic story artifacts in `artifacts/story/`; eval history in `artifacts/eval_runs/`; UI check screenshots in `artifacts/ui-check/`; `tests/test_ui_resilience.py` (3/3). Absences future work must not fabricate: UC2 new/persistent/resolved diff counts (no diff data), real `story` run artifacts on hosted Cloud (empty state possible), testimonials/customers/benchmarks/pricing.

## Product Principles

1. Deterministic first, LLM only as fallback — every claim needs typed evidence.
2. Never invent numbers, diffs, or assets — show empty/degraded states honestly.
3. Audit others and self — operator UI must pass its own a11y sweep.
4. Engine failures degrade, never blank the UI.

## Accessibility & Inclusion

Operator UI must meet standard contrast, real headings, focus-visible, forced-colors fallback (agent audits a11y elsewhere, so dogfooding required). No product-specific alternate needs established.
