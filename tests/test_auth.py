"""Unit tests: failure classification, config parsing, secret handling."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import SecretStr

from sap_agent.schemas import TRANSIENT_FAILURES, AuthFailureKind, AuthResult, Config

if TYPE_CHECKING:
    import pytest


class TestClassifyError:
    def test_timeout_classifies_as_timeout(self) -> None:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        kind = None
        try:
            raise PlaywrightTimeoutError("Timeout 30000ms exceeded")
        except Exception as exc:
            from sap_agent.tools.auth import classify_error

            kind = classify_error(exc)
        assert kind == AuthFailureKind.TIMEOUT

    def test_bad_credentials_hint_wins(self) -> None:
        from sap_agent.tools.auth import classify_error

        assert classify_error(ValueError("boom"), page_has_credentials_hint=True) == AuthFailureKind.BAD_CREDENTIALS

    def test_network_error_from_playwright_message(self) -> None:
        from playwright.sync_api import Error as PlaywrightError

        from sap_agent.tools.auth import classify_error

        kind = classify_error(PlaywrightError("net::ERR_CONNECTION_REFUSED at http://x"))
        assert kind == AuthFailureKind.NETWORK_ERROR

    def test_unknown_becomes_element_not_found(self) -> None:
        from sap_agent.tools.auth import classify_error

        assert classify_error(RuntimeError("mystery")) == AuthFailureKind.ELEMENT_NOT_FOUND


class TestTransientPolicy:
    def test_retryable_kinds(self) -> None:
        assert AuthFailureKind.TIMEOUT in TRANSIENT_FAILURES
        assert AuthFailureKind.NETWORK_ERROR in TRANSIENT_FAILURES
        assert AuthFailureKind.ELEMENT_NOT_FOUND in TRANSIENT_FAILURES

    def test_deterministic_kinds_not_retried(self) -> None:
        assert AuthFailureKind.BAD_CREDENTIALS not in TRANSIENT_FAILURES
        assert AuthFailureKind.SSO_UNSUPPORTED not in TRANSIENT_FAILURES
        assert AuthFailureKind.REDIRECT_LOOP not in TRANSIENT_FAILURES

    def test_auth_result_transient_flag(self) -> None:
        transient = AuthResult(ok=False, kind=AuthFailureKind.TIMEOUT, attempts=1)
        assert transient.transient
        fatal = AuthResult(ok=False, kind=AuthFailureKind.BAD_CREDENTIALS, attempts=1)
        assert not fatal.transient


class TestConfigFromEnv:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SAP_AGENT_URL", raising=False)
        monkeypatch.delenv("SAP_AGENT_USER", raising=False)
        monkeypatch.delenv("SAP_AGENT_PASSWORD", raising=False)
        cfg = Config.from_env()
        assert cfg.app_url == "https://jonasperegrino.github.io/sap-fiori/"
        assert not cfg.has_credentials()

    def test_env_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SAP_AGENT_URL", "https://fiori.example.com")
        monkeypatch.setenv("SAP_AGENT_USER", "alice")
        monkeypatch.setenv("SAP_AGENT_PASSWORD", "s3cret")
        cfg = Config.from_env()
        assert cfg.username == "alice"
        assert cfg.password.get_secret_value() == "s3cret"
        assert cfg.has_credentials()

    def test_overrides_win(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SAP_AGENT_URL", "https://env.example.com")
        cfg = Config.from_env(app_url="https://flag.example.com")
        assert cfg.app_url == "https://flag.example.com"

    def test_password_not_in_model_dump(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SAP_AGENT_PASSWORD", "hunter2")
        cfg = Config.from_env()
        dumped = cfg.model_dump()
        assert "hunter2" not in str(dumped)
        assert "**********" in str(dumped) or "SecretStr" in str(dumped)


class TestSecretHygiene:
    def test_auth_result_sanitized_never_holds_password(self) -> None:
        result = AuthResult(
            ok=False,
            kind=AuthFailureKind.BAD_CREDENTIALS,
            landing_url="http://localhost:8080",
            detail="credentials rejected",
            attempts=1,
        )
        serialized = result.sanitized()
        assert "password123" not in str(serialized)
        assert serialized["kind"] == "bad_credentials"

    def test_secret_field_type(self) -> None:
        cfg = Config(password=SecretStr("x"))
        assert cfg.password.get_secret_value() == "x"
        assert "x" not in str(cfg.password)


class TestLoginBackoff:
    def test_backoff_grows_exponentially_and_caps(self) -> None:
        from sap_agent.tools.auth import MAX_BACKOFF_S, _backoff_delay

        assert _backoff_delay(0.5, 1) == 0.5
        assert _backoff_delay(0.5, 2) == 1.0
        assert _backoff_delay(0.5, 3) == 2.0
        assert _backoff_delay(0.5, 4) == 4.0
        assert _backoff_delay(0.5, 5) == MAX_BACKOFF_S
        assert _backoff_delay(0.5, 50) == MAX_BACKOFF_S

    def test_zero_base_disables_backoff(self) -> None:
        from sap_agent.tools.auth import _backoff_delay

        assert _backoff_delay(0.0, 3) == 0.0

    def test_config_backoff_default_and_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SAP_AGENT_RETRY_BACKOFF_S", raising=False)
        assert Config.from_env().retry_backoff_s == 0.5
        monkeypatch.setenv("SAP_AGENT_RETRY_BACKOFF_S", "1.5")
        assert Config.from_env().retry_backoff_s == 1.5

    def test_timeout_fields_default_and_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SAP_AGENT_NAV_TIMEOUT_MS", raising=False)
        monkeypatch.delenv("SAP_AGENT_EXTRACT_TIMEOUT_MS", raising=False)
        cfg = Config.from_env()
        assert cfg.nav_timeout_ms == 10_000
        assert cfg.extract_timeout_ms == 15_000
        monkeypatch.setenv("SAP_AGENT_NAV_TIMEOUT_MS", "7000")
        monkeypatch.setenv("SAP_AGENT_EXTRACT_TIMEOUT_MS", "20000")
        cfg = Config.from_env()
        assert cfg.nav_timeout_ms == 7_000
        assert cfg.extract_timeout_ms == 20_000
