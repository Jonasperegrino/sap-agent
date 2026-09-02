# SAP Fiori Discovery Agent

Autonomous agent over a SAP Fiori / UI5 app: discovers pages + data, answers questions with typed evidence, audits accessibility/UX across every route, and files reproducible bug reports. Deterministic-by-default; LLM is an optional fallback.

Fiori demo app lives in `../fiori-app` (future separate repo/deployable).

## Quick start

```sh
uv sync
uv run playwright install chromium   # one-time

# fiori app must be up (in another terminal or via docker)
make -C ../fiori-app serve          # :8080
# or
make docker-up                       # 127.0.0.1:8080 via nginx

# run the agent
SAP_AGENT_USER=demo SAP_AGENT_PASSWORD=password123 SAP_AGENT_URL=http://localhost:8080 \
  uv run python -m sap_agent.cli login

# Streamlit operator UI
make ui   # -> http://localhost:8501 (needs fiori-app on :8080)
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

## Evaluation & quality gates

```sh
make test    # pytest + 80% coverage gate (193 tests)
make lint    # ruff
make check   # lint + test
make eval    # 19 deterministic scenarios (needs fiori-app on :8080)
make demo    # interactive QA walk with colored terminal output (auto-starts fiori-app if needed)
```

Each `make eval` run persists `artifacts/eval_runs/<ts>.json` + `history.md` trend.

## Module layout

```
agent/
  sap_agent/
    cli.py        # entry points
    controller.py # agent loop: budgets, stuck detection, planner mode
    context.py / schemas.py / memory.py
    ui5/          # UI5 bridge (page.evaluate)
    tools/        # auth, nav, extract, network, reason, answer, discover, qa, …
    ui/           # terminal.py + streamlit_app.py
  streamlit_app.py  # root shim -> sap_agent/ui/streamlit_app.py
  .streamlit/config.toml
  tests/          # behavioral unit + integration (needs fiori-app + chromium)
  evals/          # run_eval.py + scenarios.json
  docs/architecture.md
  scripts/demo.sh
```

## Deployment

- **Agent**: `uv` Python 3.12+, Playwright chromium. `streamlit_app.py` is the Streamlit entrypoint (`streamlit run streamlit_app.py` or `make ui`). Env `SAP_AGENT_URL` points at the deployed fiori-app URL. `SAP_AGENT_LLM_*` optional for NL aggregate questions.
- **Fiori app**: deploy `../fiori-app` separately (see its README/Dockerfile). This agent has no bundled fiori-app — `FIORI_APP_DIR` env (default `../fiori-app`) overrides local path for `make vendor-ui5` / `make demo` / `docker-up`.

## Config

`SAP_AGENT_URL` (default `http://localhost:8080`), `SAP_AGENT_USER`, `SAP_AGENT_PASSWORD`, `SAP_AGENT_LLM_API_KEY` / `MODEL` / `BASE_URL` / `PROVIDER` / `TIMEOUT_S`, retry/nav/extract timeouts. See `sap_agent/schemas.py:396`.
# sap-agent
