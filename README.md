# SAP Fiori Discovery Agent

Autonomous agent over a SAP Fiori / UI5 app: discovers pages + data, answers questions with typed evidence, audits accessibility/UX across every route, and files reproducible bug reports. Deterministic-by-default; LLM is an optional fallback.

Fiori demo app live at **https://jonasperegrino.github.io/sap-fiori/** (repo `../sap-fiori`, GitHub Pages). Local `../fiori-app` still works via `FIORI_APP_DIR`.

## Quick start

```sh
uv sync
uv run playwright install chromium   # one-time

# live app (no local server needed)
SAP_AGENT_USER=demo SAP_AGENT_PASSWORD=password123 \
  uv run python -m sap_agent.cli login
# SAP_AGENT_URL defaults to https://jonasperegrino.github.io/sap-fiori/
# local only: SAP_AGENT_URL=http://localhost:8080 SAP_AGENT_USER=demo SAP_AGENT_PASSWORD=password123 \
  uv run python -m sap_agent.cli login

# Deterministic video slice (no browser, no network, rules-only):
uv run python -m sap_agent.cli story   # -> artifacts/story/uc1,uc2,uc3 (pins 2026-10-05)

# Streamlit operator UI
make ui   # -> http://localhost:8501 (defaults to live app)
# Optional: restore the hidden Investigate tab with SAP_AGENT_SHOW_INVESTIGATE_TAB=1
```

Env is prefix `SAP_AGENT_*` — see `.env.example`. Credentials via env or secure prompt, never argv.

## CLI

| Command | What |
|---|---|
| `login` | verify session + auth retry behavior |
| `inspect` | dump first table + captured network URLs as JSON |
| `discover` | structured `AppSummary` JSON (pages, entities, tables) |
| `ask "<question>" [--route R]` | count/existence Q&A with evidence, confidence, checksum |
| `report` | on login failure: draft classified bug report |
| `qa [--format json|markdown]` | full audit walk: screenshots, a11y, UX, perf hints per route |
| `agent` | planner-mode loop: picks next action (login→audit…) until every route is audited |
| `story` | regenerate deterministic uc1/uc2/uc3 artifacts (rules-only, video slice) |

## Evaluation & quality gates

```sh
make test    # pytest + 80% coverage gate (450 tests)
make lint    # ruff
make check   # lint + test
make eval    # 25 deterministic scenarios (defaults to live app; SAP_AGENT_URL=http://localhost:8080 for local)
make demo    # interactive QA walk with colored terminal output
```

Each `make eval` run persists `artifacts/eval_runs/<ts>.json` + `history.md` trend.

## Demo scenario (SO-1024)

Asking why the dashboard Total Value differs from the order amounts runs a scripted investigation
(`sap_agent/demo_case.py`). Its Fiori figures are a seeded snapshot of the demo app and match the deployed
`sap-fiori` data; the ingestion log is simulated and labeled as such in the UI and the report. The dashboard
screenshot on the Bug reports tab is `sap_agent/ui/assets/so-1024-dashboard.png`.

## Module layout

```
agent/
  sap_agent/
    cli.py        # parser + config + main (handlers in cli_commands)
    cli_commands.py  # one function per subcommand
    cli_runner.py # browser session lifecycle + shared login logs
    controller.py # agent loop: budgets, stuck detection, planner mode
    context.py / schemas.py / memory.py / protocols.py
    ui5/          # UI5 bridge (page.evaluate)
    tools/        # auth, nav, extract, network, reason (+reason_data), answer (+core/aggregate/lookup), discover, qa, …
    ui/           # terminal.py + streamlit_app.py + service.py
  streamlit_app.py  # root shim -> sap_agent/ui/streamlit_app.py
  .streamlit/config.toml
  tests/          # behavioral unit + integration (needs fiori-app + chromium)
  evals/          # run_eval.py + scenarios.json
  docs/architecture.md
  scripts/demo.sh
```

## Deployment

- **Agent — Streamlit Cloud**: `streamlit_app.py` is the entrypoint. Connect repo, set Python 3.12, secrets `SAP_AGENT_URL=https://jonasperegrino.github.io/sap-fiori/` (default), `SAP_AGENT_USER`/`PASSWORD`.

Cloud install notes:
- Include `requirements.txt` (or uv.lock) and add a `packages.txt` at the repo root listing OS packages required for Playwright (an example `packages.txt` is included).
- In the Streamlit Cloud app settings, set the "Setup command" to: `.streamlit/setup.sh` (this runs `python -m playwright install chromium`).

Local: `uv run streamlit run streamlit_app.py` or `make ui`.
- **Fiori app**: live at `https://jonasperegrino.github.io/sap-fiori/` (GitHub Pages, repo `../sap-fiori`). Local fallback: `../fiori-app` or `FIORI_APP_DIR` for `make vendor-ui5` / `make demo` / `docker-up`.
- **Env**: `SAP_AGENT_LLM_*` optional for NL aggregate questions.

## Config

`SAP_AGENT_URL` (default `https://jonasperegrino.github.io/sap-fiori/`), `SAP_AGENT_USER`, `SAP_AGENT_PASSWORD`, `SAP_AGENT_LLM_API_KEY` / `MODEL` / `BASE_URL` / `PROVIDER` / `TIMEOUT_S`, retry/nav/extract timeouts. See `sap_agent/schemas.py:396`. Streamlit Cloud secrets mirror env vars via `st.secrets`.
# sap-agent
