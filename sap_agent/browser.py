"""Shared Chromium launch helpers.

Every CLI command and the Streamlit service launch Chromium through
`launch_args()`. Sandboxed envs (Streamlit Cloud, Docker as root) refuse
to start Chromium without `--no-sandbox`, and small `/dev/shm` (Cloud,
containers) crashes renderers without `--disable-dev-shm-usage` — both
hard-fail the whole agent run. The flags are harmless on a local desktop,
so they apply unconditionally: gating them behind env-var detection meant
a host that set none of the vars silently launched with bare `headless=`.
"""

from __future__ import annotations


def launch_args() -> dict:
    """Chromium args safe for sandboxed / low-shm envs and local dev."""
    return {
        "args": [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ]
    }


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
