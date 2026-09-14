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


def launch_args(*, low_memory_fallback: bool = False) -> dict:
    """Chromium args safe for sandboxed / low-shm envs and local dev.

    The base set disables the sandbox (refused by Streamlit Cloud / Docker
    as root), /dev/shm usage (tiny on Cloud/containers — crashes renderers),
    plus zygote/extensions/crash-reporter processes that only add crash
    surface in headless. When the first launch still dies (Cloud free tier
    is ~1GB RAM), the fallback adds `--single-process` — less stable in
    general, but the difference between an answer and a launch crash there.
    """
    args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--no-zygote",
        "--disable-extensions",
        "--disable-crash-reporter",
    ]
    if low_memory_fallback:
        args.append("--single-process")
    return {"args": args}


#: launch-crash signature: Chromium died during startup (missing system
#: libs or OOM-killed), not a page-level failure. Callers translate this
#: into an actionable message instead of a raw log dump.
LAUNCH_CRASH_MARKERS = (
    "Target page, context or browser has been closed",
    "Target crashed",
)


def is_launch_crash(exc: BaseException) -> bool:
    """True when `exc` looks like Chromium dying at launch, not a page error."""
    msg = str(exc)
    return "BrowserType.launch" in msg and any(m in msg for m in LAUNCH_CRASH_MARKERS)


LAUNCH_CRASH_HINT = (
    "Chromium crashed on startup on the server (missing system libraries or "
    "out of memory) — not a question the agent failed to answer. Fix: redeploy "
    "so the updated `packages.txt` installs (Cloud needs a Reboot/redeploy for "
    "apt changes), then retry."
)


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
