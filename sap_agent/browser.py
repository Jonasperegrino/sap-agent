"""Shared Chromium launch helpers (perf B1).

Every CLI command and the Streamlit service used to launch Chromium with
bare `headless=` only, so sandboxed envs (Streamlit Cloud, Docker as root)
paid a retry loop or a hard crash. This module is the single source for
launch kwargs; per-command `browser.new_page()` reuse stays next (pool).
"""

from __future__ import annotations

import os


def launch_args() -> dict:
    """Chromium args for sandboxed / low-shm envs."""
    if (
        os.environ.get("SAP_AGENT_NO_SANDBOX", "").lower() in {"1", "true", "yes"}
        or os.environ.get("STREAMLIT_RUNTIME")
        or os.environ.get("STREAMLIT_CLOUD")
    ):
        return {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    return {}


#: install commands tried in order when the browser binary is missing
INSTALL_COMMANDS: tuple[list[str], ...] = (
    ["playwright", "install", "chromium"],
    ["playwright", "install", "chromium-headless-shell"],
    ["python", "-m", "playwright", "install", "chromium"],
)


def try_install_chromium(timeout: int = 180) -> None:
    """Best-effort browser install; callers re-check existence afterwards."""
    import subprocess as _sp

    for cmd in INSTALL_COMMANDS:
        try:
            _sp.run(cmd, check=False, timeout=timeout)
        except (OSError, _sp.SubprocessError):
            continue
