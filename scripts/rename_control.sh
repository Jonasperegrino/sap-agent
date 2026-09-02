#!/usr/bin/env bash
# Rename choreography for the self-healing locator demo (issue #652).
#
#   ./scripts/rename_control.sh rename    # salesTable -> ordersTable (in place)
#   ./scripts/rename_control.sh restore   # git-restore the view file
#
# The committed state keeps `salesTable`; run `rename` only to demo that a
# hardcoded-id selector breaks while the semantic bridge stays green.
set -euo pipefail

FIORI_APP_DIR="${FIORI_APP_DIR:-../fiori-app}"
VIEW="$FIORI_APP_DIR/webapp/view/Dashboard.view.xml"

case "${1:-}" in
  rename)
    sed -i '' 's/id="salesTable"/id="ordersTable"/g' "$VIEW"
    echo "renamed: id=\"salesTable\" -> id=\"ordersTable\" in $VIEW"
    grep -n 'id="ordersTable"' "$VIEW"
    ;;
  restore)
    git checkout -- "$VIEW"
    echo "restored $VIEW (control id back to salesTable)"
    ;;
  *)
    echo "usage: $0 rename|restore" >&2
    exit 2
    ;;
esac
