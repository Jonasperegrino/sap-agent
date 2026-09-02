#!/usr/bin/env bash
# End-to-end demo (#696): serve the PoC app if needed, run the full QA
# workflow with colored terminal output, print the summary table and report
# path. Optional: DEMO_OPEN=1 opens the report file (macOS `open`).
#
# Run from the sap-poc/ directory:  make demo   (or  bash scripts/demo.sh)
set -euo pipefail

cd "$(dirname "$0")/.."

# fiori-app lives in ../fiori-app when deployed separately
FIORI_APP_DIR="${FIORI_APP_DIR:-../fiori-app}"

APP_URL="${SAP_AGENT_URL:-http://localhost:8080}"
export SAP_AGENT_USER="${SAP_AGENT_USER:-demo}"
export SAP_AGENT_PASSWORD="${SAP_AGENT_PASSWORD:-password123}"
export SAP_AGENT_URL="$APP_URL"

STARTED_SERVER=0
cleanup() {
  if [ "$STARTED_SERVER" = "1" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    echo "demo: stopped the temporary server"
  fi
}
trap cleanup EXIT

wait_for_app() {
  local i
  for i in {1..30}; do
    if curl -fsS -o /dev/null "$APP_URL/" 2>/dev/null; then
      return 0
    fi
    sleep 0.5
  done
  echo "demo: app did not come up at $APP_URL" >&2
  exit 1
}

if ! curl -fsS -o /dev/null "$APP_URL/" 2>/dev/null; then
  echo "demo: no server at $APP_URL — starting one ($FIORI_APP_DIR)"
  python3 -m http.server 8080 -d "$FIORI_APP_DIR" >/tmp/poc_serve.log 2>&1 &
  SERVER_PID=$!
  STARTED_SERVER=1
  wait_for_app
fi

if [ ! -f "$FIORI_APP_DIR/resources/sap-ui-core.js" ]; then
  echo "demo: vendoring OpenUI5 runtime (one-time download, ~1 min)"
  bash "$FIORI_APP_DIR/scripts/vendor_ui5.sh"
fi

echo "demo: running full QA workflow against $APP_URL"
uv run python -m sap_agent.cli qa

REPORT="artifacts/qa_report.json"
if [ "${DEMO_OPEN:-0}" = "1" ] && [ -f "$REPORT" ]; then
  echo "demo: opening report in default viewer"
  open "$REPORT"
fi
