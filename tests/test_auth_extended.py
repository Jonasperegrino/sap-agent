"""Extended unit tests for auth tool (issue #645)."""

from __future__ import annotations

import pytest
from playwright.sync_api import Error as PlaywrightError

from sap_agent.schemas import AuthFailureKind, AuthResult
from sap_agent.tools.auth import AuthError, classify_error, validate_app_url


class TestValidateAppUrl:
    def test_valid_http(self) -> None:
        assert validate_app_url("http://localhost:8080") == "http://localhost:8080"

    def test_valid_https(self) -> None:
        assert validate_app_url("https://fiori.example.com") == "https://fiori.example.com"

    def test_invalid_scheme(self) -> None:
        with pytest.raises(ValueError, match="invalid app URL"):
            validate_app_url("ftp://example.com")

    def test_no_scheme(self) -> None:
        with pytest.raises(ValueError, match="invalid app URL"):
            validate_app_url("localhost:8080")

    def test_no_netloc(self) -> None:
        with pytest.raises(ValueError, match="invalid app URL"):
            validate_app_url("http://")


class TestAuthError:
    def test_carries_result(self) -> None:
        result = AuthResult(ok=False, kind=AuthFailureKind.BAD_CREDENTIALS, detail="bad creds", attempts=1)
        exc = AuthError(result)
        assert exc.result is result
        assert "bad creds" in str(exc)

    def test_is_exception(self) -> None:
        result = AuthResult(ok=False, kind=AuthFailureKind.TIMEOUT, attempts=1)
        exc = AuthError(result)
        assert isinstance(exc, Exception)


class TestClassifyErrorExtended:
    def test_playwright_error_without_net(self) -> None:
        err = PlaywrightError("strict mode violation: multiple elements")
        assert classify_error(err) == AuthFailureKind.ELEMENT_NOT_FOUND

    def test_builtin_timeout(self) -> None:
        assert classify_error(TimeoutError("timed out")) == AuthFailureKind.TIMEOUT

    def test_element_not_found_without_hint(self) -> None:
        assert classify_error(RuntimeError("selector not found")) == AuthFailureKind.ELEMENT_NOT_FOUND
