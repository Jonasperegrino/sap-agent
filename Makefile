# agent Makefile — SAP Fiori discovery + Q&A agent + Streamlit UI
# Fiori app lives in ../fiori-app (separate deployable). Set FIORI_APP_DIR to override.

FIORI_APP_DIR ?= ../fiori-app

.PHONY: help setup ui test lint eval check demo vendor-ui5 docker-up docker-down precommit pre-commit

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: ## Install deps + browser (one-time)
	uv sync
	@if ! uv run playwright --version >/dev/null 2>&1 || ! ls $$(uv run python -c "import pathlib, playwright; print(pathlib.Path(playwright.__file__).parent)")/driver/package/lib/server/chromium* >/dev/null 2>&1; then \
		echo "setup: installing Playwright Chromium…"; \
		uv run playwright install chromium; \
	else echo "setup: Playwright Chromium already installed"; fi
	@if [ ! -f $(FIORI_APP_DIR)/resources/sap-ui-core.js ]; then \
		echo "setup: vendoring OpenUI5…"; bash $(FIORI_APP_DIR)/scripts/vendor_ui5.sh; \
	else echo "setup: UI5 runtime already vendored"; fi
	@echo "setup: done — try 'make demo' or 'make ui' (needs fiori-app on :8080)"

ui: ## Start Streamlit operator UI
	uv run streamlit run streamlit_app.py

test: ## Run tests with coverage (80% gate)
	uv run pytest --cov=sap_agent --cov-report=term-missing --cov-fail-under=80

lint: ## Lint with ruff
	uv run ruff check .

eval: ## Run evaluation scenarios (needs fiori-app on :8080)
	uv run python evals/run_eval.py

check: lint test ## Lint + test

precommit: ## Run pre-commit hooks on all files
	uv run pre-commit run --all-files

pre-commit: precommit ## Alias for precommit

demo: ## Run interactive QA demo (auto-starts fiori-app if needed)
	bash scripts/demo.sh

vendor-ui5: ## Vendor UI5 into fiori-app
	bash $(FIORI_APP_DIR)/scripts/vendor_ui5.sh

docker-up: ## Start fiori-app Docker
	docker compose -f $(FIORI_APP_DIR)/docker-compose.yml up -d --build

docker-down: ## Stop fiori-app Docker
	docker compose -f $(FIORI_APP_DIR)/docker-compose.yml down
