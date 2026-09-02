# Plan: Multipage Fiori PoC — Customers, Product Catalog, Order History (2026)

Status: **Implemented** (2026-08). This was the build plan for the multipage
extension; §3.9–§3.10 of `architecture.md` record what shipped. The "current
state" section below describes the pre-extension app and is kept for history.
Scope: `sap-poc/` only. App extension first (data + pages), then agent support,
then tests/evals. No backend: static JSON + client-side state, matching the
existing architecture (D1, M1: "dashboard data moves to served JSON/CSV").

## 0. Current state (verified)

**App (`app/`)**
- UI5 app `sap.fiori.poc`, OpenUI5 from CDN. Routing: `App` root view with
  `pages` aggregation; routes `""`→Login, `"dashboard"`→Dashboard.
- Login.controller: `demo`/`password123` → `navTo("dashboard")`.
- Dashboard: one table `salesTable`, model `orders>` fetched from
  `data/sales.json` (4 orders). Columns: Order ID, Customer, Amount, Status, Built.
- Data: flat JSON served statically (http.server or nginx). No backend, no OData.

**Agent (`sap_agent/`)**
- Deterministic, rule-based Q&A. `login` lands on `#/dashboard`
  (`success_route`). `evaluate_question` snapshots **the first table on the
  current page** (`.first` locator). No navigation tool exists (`tools/nav.py`
  is missing — README/ADR list it, files don't).
- `discover_app` inspects only the current page; `_areas` = current hash route.
- `reason.KNOWN_COLUMNS` hardcoded: `status, customer, amount, built`.
- Year comparer: `cell[:4] == value` — already 2026-compatible.

**Tests/evals** hardcode golden data: 4 orders, exact 5 columns, `Approved=2`,
`built in 2026=3`, `Cancelled→not_found`.

## 1. Data model (Phase 0)

New/updated files under `app/data/`, same style (flat JSON arrays, id first key,
`€X,XXX.XX` amount strings).

| File | Content | Shape |
| --- | --- | --- |
| `customers.json` | ~8 customers | `id` (C-1001…), `name`, `city`, `country`, `contact`, `email`, `since` (yyyy-mm-dd), `creditRating` |
| `products.json` | ~10–12 products | `id` (PRD-1001…), `name`, `category`, `price` (€ string), `stock` (int), `unit`, `description`, `active` (bool) |
| `sales.json` | extend 4 → ~12 orders | keep existing fields exactly; **add** `customerId` FK (keep `customer` name for display + backward compat); spread `built` across 2025 (1–2) and 2026 (8–10); add `Rejected`/`Cancelled` statuses |

Design rules:
- `id` stays the **first key** in every dict — discover.py's data-overlap PK
  matching (first key of payload vs first rendered column) keeps working.
- Dashboard columns stay **unchanged** (Customer shows name; `customerId` is
  payload-only) → zero column churn, existing column asserts survive.
- Products keep one intentionally hidden/inactive entry (`active:false` or
  `stock:0`) — gives a "rendered ⊂ payload, no hallucination" test surface.

## 2. Multipage shell (Phase 1)

`app/webapp/manifest.json` — add routes/targets:

```
""                → login
"dashboard"       → Dashboard          (unchanged)
"catalog"         → Catalog            (new)
"orders"          → OrderHistory       (new)
"customer/{customerId}" → CustomerDetail (new, mandatory `customerId` param)
```

- Each page gets a header `Toolbar` with nav buttons (Dashboard / Catalog /
  Order History) → `router.navTo(...)`. Semantic `.sapMBtn` selectors already
  picked up by discovery's `ACTION_SELECTOR`.
- Detail page back button → `router.navBack()`.
- No shell/IconTabBar — flat toolbar buttons keep the DOM simple and
  agent-parseable.

## 3. Pages (Phase 2)

All controllers follow the existing pattern: `fetch("data/<file>.json")` →
`JSONModel` → `catch` → `MessageToast` + empty model. Never hardcoded payloads.

### 3.1 OrderHistory (`orders` view + controller)
- Fetch `sales.json`, **filter `built.startsWith("2026")`** in controller
  ("this year 2026 only" — static, no datepicker).
- Merge orders created via the catalog (localStorage, see 3.2) into the same
  table.
- Columns identical to dashboard table → agent answers transfer directly.

### 3.2 Catalog (`catalog` view + controller)
- Fetch `products.json` → table: ID, Name, Category, Price, Stock, plus a
  per-row quantity input + "Add to Order" button.
- Filter out `active:false` / `stock:0` rows from the table (visible dataset).
- **Place Order**: validate qty ≥ 1 and qty ≤ stock; build order
  `{id: SO-2001+ (monotonic, read max existing + 1), customer: "Acme Corp",
  customerId: "C-1001", amount: price×qty, status: "Pending",
  built: <today 2026-08-20>}`; persist to
  `localStorage["poc.orders.created"]`; confirm toast; clear form.
- **Amount formatting**: prices stored as integer euros internally (e.g.
  `1250`); render + total via one shared formatter producing `€1,250.00`
  (Intl.NumberFormat or manual — single helper used by Catalog, Dashboard,
  History, Detail so full-table column formats are byte-identical to the
  fixture style). Fractional-euro prices would break this — excluded.
- **No backend** — localStorage survives reload on static hosting. This is the
  documented simplification.
- **Known limitation**: login is client-side only; a direct reload on
  `#/customer/...` renders without auth. Acceptable for PoC.

### 3.3 CustomerDetail (`customer/{customerId}` view + controller)
- Fetch `customers.json` + `sales.json`; resolve param → ObjectHeader (name,
  city, country, contact, credit rating) + KPI text (total orders, total
  amount) + table of that customer's orders (filter by `customerId`).
- Handles unknown id → empty state + toast (exercises failure paths).

## 4. Agent support (Phase 3)

Minimal companion work so the agent stops assuming "one page, first table".

1. **`tools/nav.py` (new, ADR D4 lists it but it was never built)**
   - `navigate(page, route)`: click the matching nav button by text
     (semantic), fallback to `page.goto(app_url + "#/<route>")`.
   - `open_row_detail(page, row_text)`: click dashboard row → wait for
     `#/customer/...` hash. `go_back(page)` → `navBack` + wait for dashboard.
2. **Page-aware answering** — `evaluate_question(..., route=...)`: navigate to
   route before snapshot if given (default: current page — backward
   compatible). Lets "how many products" answer against the catalog table and
   "how many orders" against history.
3. **Extend `reason.KNOWN_COLUMNS`** to the new pages:
   `status, customer, amount, built, price, stock, category, product, qty,
   quantity, unit`.
4. **Multi-area discovery** — `discover_app` walks the menu: for each nav
   button (Dashboard → Catalog → Order History) navigate → `get_all_tables` →
   back; **then click the first dashboard row → CustomerDetail → back**, so the
   detail table is discovered too. `_areas` must aggregate every visited route
   (it currently returns only the current hash). Result: entities `sales`,
   `customers`, `products`; tables: dashboard, catalog, history, customer
   detail (4).
   - **Linking caveat**: the detail page's orders table renders order ids →
     links to `sales.json`, never to `customers.json` (no page renders
     customer-id as first column). The `customers` entity therefore has
     `tables: []` — assert it via `endpoints`, not `tables` (see §5).

Out of scope for M2: LOOKUP execution (resolving "find Acme Corp" → detail
page), LLM intent parsing, MCP. Keep rule-based.

## 5. Tests & evals (Phase 4)

### Golden-value updates (breaking — deliberate)
- `sales.json` 4 → 12 rows: `count_total` 4 → 12.
- `built in 2026` 3 → N (N = number of 2026 fixtures).
- `Approved` 2 → N2 (ask-status + eval + `test_answer_integration`).
- `Cancelled` — now *found* if cancelled orders added → repoint the not_found
  eval scenario + any status-specific tests to a value absent everywhere
  (e.g. `Returned`). Note: `test_ask_integration`'s `Purple` not_found test is
  already absent-everywhere — unchanged.
- `test_data_integrity`: `len(FIXTURE)==4` → 12; payload now has `customerId`.
- `columns_superset` eval stays valid (dashboard columns unchanged).

### New unit tests
- `reason`: new column vocabulary parses `price`, `stock`, `category`, `qty`.
- `nav`: route-string parsing, back-navigation semantics.

### New integration tests (`tests/`, marker `integration`, need `make serve`)
1. **OrderHistory 2026 filter** — page shows only 2026 orders; SO-1003 (2025)
   absent; "how many orders are there?" on history page = 2026 count, on
   dashboard = 12 (page-context sensitivity — same question, different page,
   different answer).
2. **Catalog → order round-trip** — add product qty=2 → toast → order appears
   in order history with status Pending and amount = 2×price.
3. **Customer detail navigation** — click row → hash `#/customer/C-10xx`,
   header name matches, orders table ⊂ filtered payload (FK integrity).
4. **Back navigation** — detail → back → dashboard table intact.
5. **Validation** — qty 0 / qty > stock → no order created, error visible.
6. **Hidden product** — inactive product in payload but NOT rendered; asking
   "how many products" answers rendered count (no hallucination).
7. **Determinism** — new questions answered 3× → identical checksum.
8. **Trace secrecy** — customers/products payload values absent from trace.

### New eval scenarios (`evals/scenarios.json`)
- `ask-products-total`: "how many products are there" → rendered count.
- `ask-orders-2026-history`: navigate to history, "how many orders" → 2026 count.
- `ask-customer-orders`: "how many orders for Acme Corp" (customer column).
- `discover-multiarea`: `discover` expects entities `sales` and `products`
  **with tables** plus a `customers` entity present (assert via `endpoints`;
  it carries no linked table by construction), and ≥3 tables overall.
- `ask-not-found-new`: repointed to truly-absent value.
- `order-roundtrip`: **implemented as a pytest integration test** (localStorage
  order → history shows it → count Pending) — no run_eval harness change needed;
  run_eval stays a plain CLI-subprocess runner.

## 6. Docs (Phase 5)

- `README.md`: new pages, new data files, updated golden answers.
- `docs/architecture.md`: note `tools/nav.py` implemented (ADR D4 gap closed),
  multi-area discovery, page-aware answering.

## 7. Verification (Phase 6)

1. `make serve` (ask first — long-lived) from `sap-poc/`.
2. Manual smoke: login → each menu item → detail → order round-trip.
3. `uv run pytest` (unit green without server; integration with server).
4. `uv run ruff check .`
5. `uv run python evals/run_eval.py` → all scenarios pass.
6. `docker compose up` variant for the nginx path (optional).

## 8. Suggested test-case ideas (beyond the above)

| # | Case | Why interesting |
| --- | --- | --- |
| 1 | Same question on different pages answers differently (dashboard vs history) | Agent must respect current view, not globals |
| 2 | Interaction → state → QA chain (place order, then count pending) | Combines action execution + deterministic answer; tests localStorage persistence |
| 3 | FK integrity: detail-page orders ⊆ payload filtered by customerId | Cross-endpoint data consistency |
| 4 | Rendered ⊂ payload (hidden/inactive product) | Anti-hallucination: agent counts what's visible, not what the endpoint serves |
| 5 | Validation dialog on qty > stock | Agent's failure taxonomy: `backend_error` vs product bug vs agent limitation |
| 6 | Empty state (no results after filter) | `empty_state` classification path (#648) exercised on a non-auth surface |
| 7 | Year-boundary filter: 2025 order visible on dashboard, invisible on history | Date-filter correctness + agent's `year` comparer in a filtered context |
| 8 | Determinism checksum across new pages | Extends proven reproducibility to multi-page |
| 9 | Menu walk discovery (3 entities) | Multi-area discovery beyond the dashboard-only summary |
| 10 | Selector robustness on new pages (rename control → still green) | D2.5 semantic-first discipline holds on 4 pages |
| 11 | Back/persistence: reload on `#/customer/...` keeps order data | Router + state recovery |
| 12 | Trace secrecy for new endpoints | No data-bleed extension of existing guarantee |

Recommended shortlist for M2: 1, 2, 3, 4, 6, 8, 9.
