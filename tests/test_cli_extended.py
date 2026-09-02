"""Extended unit tests for CLI (issue #684): parser, config resolution, helpers."""

from __future__ import annotations

import pytest

from sap_agent.cli import _build_parser, _resolve_config, _succeeded
from sap_agent.schemas import StepResult, StepStatus


class TestBuildParser:
    def test_login_subcommand(self) -> None:
        args = _build_parser().parse_args(["login"])
        assert args.command == "login"

    def test_ask_status_subcommand(self) -> None:
        args = _build_parser().parse_args(["ask-status", "Approved"])
        assert args.command == "ask-status"
        assert args.status == "Approved"

    def test_ask_subcommand(self) -> None:
        args = _build_parser().parse_args(["ask", "how many orders?"])
        assert args.command == "ask"
        assert args.question == "how many orders?"

    def test_ask_with_route(self) -> None:
        args = _build_parser().parse_args(["ask", "q", "--route", "catalog"])
        assert args.route == "catalog"

    def test_qa_with_format(self) -> None:
        args = _build_parser().parse_args(["--app", "http://x", "qa", "--format", "json"])
        assert args.format == "json"

    def test_qa_default_format(self) -> None:
        args = _build_parser().parse_args(["qa"])
        assert args.format is None

    def test_timeout_flag(self) -> None:
        args = _build_parser().parse_args(["--timeout", "5000", "login"])
        assert args.timeout_ms == 5000

    def test_no_color_flag(self) -> None:
        args = _build_parser().parse_args(["--no-color", "qa"])
        assert args.no_color is True


class TestSucceeded:
    def test_finds_matching_step(self) -> None:
        history = [StepResult(tool="auth", action="login", status=StepStatus.SUCCESS)]
        assert _succeeded(history, "auth", "login") is True

    def test_no_match(self) -> None:
        history = [StepResult(tool="auth", action="login", status=StepStatus.SUCCESS)]
        assert _succeeded(history, "qa", "audit.dashboard") is False

    def test_failed_step_not_counted(self) -> None:
        history = [StepResult(tool="auth", action="login", status=StepStatus.FAILURE)]
        assert _succeeded(history, "auth", "login") is False

    def test_empty_history(self) -> None:
        assert _succeeded([], "auth", "login") is False


class TestResolveConfig:
    def test_timeout_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SAP_AGENT_USER", "u")
        monkeypatch.setenv("SAP_AGENT_PASSWORD", "p")
        import argparse

        args = argparse.Namespace(app_url="http://x", username="u", timeout_ms=5000)
        config = _resolve_config(args)
        assert config.login_timeout_ms == 5000
        assert config.nav_timeout_ms == 5000
        assert config.extract_timeout_ms == 5000

    def test_timeout_floor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SAP_AGENT_USER", "u")
        monkeypatch.setenv("SAP_AGENT_PASSWORD", "p")
        import argparse

        args = argparse.Namespace(app_url="http://x", username="u", timeout_ms=100)
        config = _resolve_config(args)
        assert config.login_timeout_ms == 1000  # floored to 1000

    def test_no_timeout_preserves_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SAP_AGENT_USER", "u")
        monkeypatch.setenv("SAP_AGENT_PASSWORD", "p")
        import argparse

        args = argparse.Namespace(app_url="http://x", username="u", timeout_ms=None)
        config = _resolve_config(args)
        assert config.login_timeout_ms == 30_000
