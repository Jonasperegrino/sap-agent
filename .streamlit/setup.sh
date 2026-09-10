#!/usr/bin/env bash
set -euo pipefail

# Streamlit Cloud setup helper — install Playwright browsers after Python deps.
# In the Cloud app settings, set the "Setup command" to: .streamlit/setup.sh

python -m playwright install chromium
