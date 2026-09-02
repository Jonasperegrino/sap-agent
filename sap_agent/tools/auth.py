"""Auth tool: login, failure taxonomy, retry policy, session handling (issue #645).

Design rules:
- credentials come from Config only; never accept them via trace/log paths
- failure taxonomy yields structured AuthResult (typed, sanitized)
- transient failures (timeout, element-not-found, network) retry within budget
  with a full page reset; deterministic failures (bad credentials, SSO
  unsupported, redirect loop) fail fast without retry
- session cookie persists in the Playwright browser context for the run only
"""

from __future__ import annotations

import contextlib
import logging
import time
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..context import SessionContext
from ..schemas import AuthFailureKind, AuthResult, Config
from ..ui5 import bridge

logger = logging.getLogger(__name__)

#: when credentials are wrong, the PoC shows this toast
BAD_CREDENTIALS_TOAST = "Invalid credentials"

#: upper bound for the exponential retry delay (#680)
MAX_BACKOFF_S = 5.0


def _backoff_delay(base_s: float, attempt: int) -> float:
    """Exponential backoff for the given 1-based attempt, capped at MAX_BACKOFF_S."""
    return min(base_s * (2 ** (attempt - 1)), MAX_BACKOFF_S)


class AuthError(Exception):
    """Raised when authentication cannot complete; carries a typed result."""

    def __init__(self, result: AuthResult) -> None:
        super().__init__(result.detail)
        self.result = result


def classify_error(error: BaseException, page_has_credentials_hint: bool = False) -> AuthFailureKind:
    """Map low-level failures to the taxonomy.

    `page_has_credentials_hint` is set when the login form remained visible after
    submission (bad-credentials signal for the PoC).
    """
    if page_has_credentials_hint:
        return AuthFailureKind.BAD_CREDENTIALS
    if isinstance(error, PlaywrightTimeoutError):
        return AuthFailureKind.TIMEOUT
    if isinstance(error, PlaywrightError):
        msg = str(error).lower()
        if "net::" in msg or "connection" in msg:
            return AuthFailureKind.NETWORK_ERROR
        # e.g. "Timeout 30000ms exceeded" is already covered; selector misses
        # surface as PlaywrightError "strict mode violation" / "locator.wait_for"
        return AuthFailureKind.ELEMENT_NOT_FOUND
    if isinstance(error, TimeoutError):
        return AuthFailureKind.TIMEOUT
    return AuthFailureKind.ELEMENT_NOT_FOUND


def _login_outcome(page: Page, success_hash: str, timeout_ms: int = 10_000) -> tuple[str, str]:
    """Determine post-submit outcome.

    1. Probe the bad-creds toast first (appears fast, auto-dismisses).
    2. Otherwise wait for the hash route to change to the dashboard.

    Returns (outcome, detail): ("bad_credentials", toast) / ("success", route)
    / ("timeout", "").
    """
    try:
        page.get_by_text(BAD_CREDENTIALS_TOAST, exact=False).first.wait_for(state="visible", timeout=2_000)
        return ("bad_credentials", BAD_CREDENTIALS_TOAST)
    except (PlaywrightTimeoutError, PlaywrightError):
        pass
    try:
        page.wait_for_url(f"**{success_hash}", timeout=timeout_ms)
        return ("success", success_hash)
    except (PlaywrightTimeoutError, PlaywrightError):
        return ("timeout", "")


def login(page: Page, config: Config, ctx: SessionContext) -> AuthResult:
    """Log into the Fiori app with bounded retries.

    Raises AuthError with a typed, sanitized result when login cannot succeed.
    """
    ctx.record("auth", "login.start", "navigating", url=config.app_url)
    attempts = 0
    redirect_seen: set[str] = set()
    last_kind: AuthFailureKind = AuthFailureKind.TIMEOUT

    while attempts < config.retry_budget:
        attempts += 1
        try:
            page.goto(config.app_url, wait_until="domcontentloaded", timeout=config.login_timeout_ms)
            bridge.wait_for_ui5_ready(page, config.login_timeout_ms)

            if not bridge.has_login_form(page, timeout_ms=10_000):
                # No plain form: could already be on a shell (session reused) or SSO boundary.
                route = bridge.current_route(page)
                if config.success_route and route and route.startswith(config.success_route):
                    ctx.record("auth", "login.ok", "session reused", url=page.url)
                    return AuthResult(ok=True, landing_url=page.url, attempts=attempts, verified_route=route)
                raise AuthError(
                    AuthResult(
                        ok=False,
                        kind=AuthFailureKind.ELEMENT_NOT_FOUND,
                        landing_url=page.url,
                        detail="login form not found; possible SSO boundary or unknown app layout",
                        attempts=attempts,
                    )
                )

            bridge.fill_login_form(page, config.username, config.password.get_secret_value())
            ctx.record("auth", "login.submitted", "credentials submitted")

            outcome, detail = _login_outcome(
                page, success_hash=config.success_route or "#/dashboard", timeout_ms=10_000
            )
            if outcome == "bad_credentials":
                result = AuthResult(
                    ok=False,
                    kind=AuthFailureKind.BAD_CREDENTIALS,
                    landing_url=page.url,
                    detail=detail or "credentials rejected by the app",
                    attempts=attempts,
                )
                ctx.record("auth", "login.failed", result.kind.value, url=page.url)
                raise AuthError(result)

            landing_route = bridge.current_route(page)
            if outcome == "success" or (
                config.success_route and landing_route and landing_route.startswith(config.success_route)
            ):
                ctx.record("auth", "login.ok", "landing verified", url=page.url)
                return AuthResult(
                    ok=True,
                    landing_url=page.url,
                    attempts=attempts,
                    verified_route=bridge.current_route(page),
                )

            # No success, no explicit rejection: treat as uncertain/redirect-loop-ish.
            route = bridge.current_route(page) or page.url
            if route in redirect_seen:
                raise AuthError(
                    AuthResult(
                        ok=False,
                        kind=AuthFailureKind.REDIRECT_LOOP,
                        landing_url=page.url,
                        detail="navigation returned to a previously visited route",
                        attempts=attempts,
                    )
                )
            redirect_seen.add(route)
            raise AuthError(
                AuthResult(
                    ok=False,
                    kind=AuthFailureKind.TIMEOUT,
                    landing_url=page.url,
                    detail="no landing marker within wait window",
                    attempts=attempts,
                )
            )

        except AuthError as exc:
            if not exc.result.transient:
                ctx.record("auth", "login.failed", exc.result.kind.value, url=page.url)
                raise exc
            ctx.record(
                "auth",
                "login.retry",
                f"transient {exc.result.kind.value}",
                url=page.url,
            )
            delay = _backoff_delay(config.retry_backoff_s, attempts)
            ctx.record("auth", "login.backoff", f"{delay:.1f}s before retry")
            time.sleep(delay)
            continue

        except (PlaywrightTimeoutError, PlaywrightError, TimeoutError) as exc:
            kind = classify_error(exc)
            last_kind = kind
            ctx.record("auth", "login.failed", f"{kind.value} (attempt {attempts})", url=page.url)
            with contextlib.suppress(PlaywrightTimeoutError, PlaywrightError):
                page.goto("about:blank", wait_until="commit")
            if kind not in {AuthFailureKind.TIMEOUT, AuthFailureKind.NETWORK_ERROR}:
                raise AuthError(
                    AuthResult(
                        ok=False,
                        kind=kind,
                        landing_url=page.url,
                        attempts=attempts,
                        detail=f"login failed: {kind.value}",
                    )
                ) from exc
            delay = _backoff_delay(config.retry_backoff_s, attempts)
            ctx.record("auth", "login.backoff", f"{delay:.1f}s before retry")
            time.sleep(delay)
            continue

    raise AuthError(
        AuthResult(
            ok=False,
            kind=last_kind,
            landing_url=page.url,
            detail=f"login retry budget exhausted ({last_kind.value})",
            attempts=attempts,
        )
    )


def validate_app_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid app URL: {url!r}")
    return url
