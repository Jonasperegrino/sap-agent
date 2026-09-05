"""CLI browser sessions (split from cli.py): one place that opens/closes Chromium.

Every command follows launch → new page → capture → work → close. The helper
below owns that lifecycle so command bodies only contain their own logic.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from playwright.sync_api import sync_playwright

from .browser import launch_args
from .context import SessionContext
from .tools.network import NetworkCapture

if TYPE_CHECKING:
    import logging
    from collections.abc import Iterator

    from .protocols import PageLike
    from .schemas import AuthResult, Config


@contextlib.contextmanager
def browser_session(config: Config) -> Iterator[tuple[PageLike, NetworkCapture, SessionContext]]:
    """Launch Chromium, yield (page, capture, ctx), always close the browser."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=config.headless, **launch_args())
        ctx = SessionContext(config)
        try:
            page = browser.new_page()
            yield page, NetworkCapture(page, config.app_url), ctx
        finally:
            browser.close()


def log_login(result: AuthResult, logger: logging.Logger) -> None:
    """One-line success log shared by every command after login."""
    logger.info(
        "login ok: %s (route %s, attempts %d)",
        result.landing_url,
        result.verified_route,
        result.attempts,
    )


def log_auth_error(result: AuthResult, logger: logging.Logger) -> None:
    """One-line failure log shared by every command's AuthError handler."""
    logger.error("login failed: %s (%s, attempts %d)", result.detail, result.kind_value(), result.attempts)
