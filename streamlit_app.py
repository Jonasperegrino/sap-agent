"""Project-root entry point for the Streamlit operator UI."""

import runpy
from pathlib import Path

# NOTE: execute the real app fresh on EVERY script run — do NOT
# `from sap_agent.ui.streamlit_app import *` here. Imported modules are
# cached in sys.modules, so an import-based entrypoint executes the app
# only for the first session in each server process; every later session
# silently renders an empty page (black with this theme) with no error.
# runpy.run_path re-executes the file unconditionally on each run.
runpy.run_path(
    str(Path(__file__).parent / "sap_agent" / "ui" / "streamlit_app.py"),
    run_name="__main__",
)
