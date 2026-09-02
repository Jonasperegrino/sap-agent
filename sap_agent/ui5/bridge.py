"""UI5 bridge: semantic reads of the running UI5 app via page.evaluate.

Kept thin on purpose (architecture D2): it is the single swap point if a
playwright-sap strategy is later preferred. Selectors here target the PoC's
plain login form; production variants are layered on top in tools/auth.py.
"""

from __future__ import annotations

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

LOGIN_USER_SELECTOR = 'input[placeholder="Username"]'
LOGIN_PASSWORD_SELECTOR = 'input[placeholder="Password"]'
LOGIN_SUBMIT_SELECTOR = 'button:has-text("Log In")'
WELCOME_HEADER_SELECTOR = ".sapMObjectHeaderTitle, .sapMOHTitle"


def wait_for_ui5_ready(page: Page, timeout_ms: int = 30_000) -> None:
    page.wait_for_selector(".sapUiBody", state="attached", timeout=timeout_ms)


def current_route(page: Page) -> str | None:
    """Return the UI5 hash route (e.g. '#/dashboard') or None."""
    url = page.url
    if "#" in url:
        return url.split("#", 1)[1]
    return None


def has_login_form(page: Page, timeout_ms: int = 5_000) -> bool:
    try:
        page.wait_for_selector(LOGIN_USER_SELECTOR, state="attached", timeout=timeout_ms)
        return True
    except PlaywrightTimeoutError:
        return False


def fill_login_form(page: Page, username: str, password: str, timeout_ms: int = 10_000) -> None:
    user = page.locator(LOGIN_USER_SELECTOR)
    pwd = page.locator(LOGIN_PASSWORD_SELECTOR)
    btn = page.locator(LOGIN_SUBMIT_SELECTOR)
    user.wait_for(state="visible", timeout=timeout_ms)
    pwd.wait_for(state="visible", timeout=timeout_ms)
    user.fill(username)
    # Playwright fills the field; the raw value is not exposed to logs by this bridge.
    pwd.fill(password)
    btn.click()


def welcome_text(page: Page, timeout_ms: int = 10_000) -> str | None:
    try:
        el = page.locator(WELCOME_HEADER_SELECTOR).first
        el.wait_for(state="visible", timeout=timeout_ms)
        return el.inner_text().strip()
    except PlaywrightTimeoutError:
        return None
