"""Project-root entry point for the Streamlit operator UI."""

import contextlib
import runpy
from pathlib import Path

_ROOT = Path(__file__).parent

# After a deploy, forget cached sap_agent modules so the page and the modules it
# imports come from the same commit (no manual reboot). Never blocks the UI.
with contextlib.suppress(Exception):
    from sap_agent.ui.reload import purge_stale_modules

    purge_stale_modules(_ROOT / "sap_agent")

# NOTE: execute the real app fresh on EVERY script run — do NOT
# `from sap_agent.ui.streamlit_app import *` here. Imported modules are
# cached in sys.modules, so an import-based entrypoint executes the app
# only for the first session in each server process; every later session
# silently renders an empty page (black with this theme) with no error.
# runpy.run_path re-executes the file unconditionally on each run.
runpy.run_path(
    str(_ROOT / "sap_agent" / "ui" / "streamlit_app.py"),
    run_name="__main__",
)
