"""CLI entry points (architecture D6): login, ask, discover.

Credentials: SAP_AGENT_URL / SAP_AGENT_USER env or flags; password from
SAP_AGENT_PASSWORD env or an interactive secure prompt. Never from argv.

Command bodies live in cli_commands; browser lifecycle in cli_runner.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys

from .cli_commands import (
    _succeeded as _succeeded,
)
from .cli_commands import (
    cmd_agent,
    cmd_ask,
    cmd_ask_status,
    cmd_discover,
    cmd_inspect,
    cmd_login,
    cmd_qa,
    cmd_report,
)
from .schemas import Config
from .tools.auth import validate_app_url

logger = logging.getLogger("fiori-agent")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fiori-agent", description="SAP Fiori discovery agent")
    parser.add_argument("--app", dest="app_url", help="Fiori app URL (env SAP_AGENT_URL)")
    parser.add_argument("--user", dest="username", help="username (env SAP_AGENT_USER)")
    parser.add_argument("--no-color", dest="no_color", action="store_true", help="disable ANSI terminal colors")
    parser.add_argument(
        "--timeout",
        dest="timeout_ms",
        type=int,
        default=None,
        help="base wait window in ms; overrides nav/extract/login timeouts (#676)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="log in and verify session")
    sub.add_parser("inspect", help="log in, capture network, dump table + trace")
    sub.add_parser("discover", help="log in and produce structured AppSummary JSON")
    ask = sub.add_parser("ask-status", help="count rows by status with evidence")
    ask.add_argument("status", help="status value to count (e.g. Approved)")
    askq = sub.add_parser("ask", help="answer a natural-language question")
    askq.add_argument("question", help="e.g. 'how many orders were built in 2026'")
    askq.add_argument(
        "--route",
        default=None,
        help="top-level page to answer against: dashboard | customers | catalog | orders | customer (default: current page)",  # noqa: E501
    )
    sub.add_parser("report", help="attempt login; on failure draft a bug report")
    qa = sub.add_parser("qa", help="run the full QA audit across every page (screenshot, a11y, UX)")
    qa.add_argument(
        "--format",
        dest="format",
        choices=("json", "markdown"),
        default=None,
        help="stdout output format (default: colored terminal summary); "
        "artifacts/qa_report.json + .md are always written",
    )
    sub.add_parser(
        "agent",
        help="planner-mode agent loop: pick login/audit actions until every route is audited (#693)",
    )
    return parser


def _resolve_config(args: argparse.Namespace) -> Config:
    from pydantic import SecretStr

    config = Config.from_env(app_url=args.app_url, username=args.username)
    if not config.has_credentials():
        if not config.username:
            config.username = input("Username: ").strip()
        if not config.password.get_secret_value():
            config.password = SecretStr(getpass.getpass("Password: "))
    validate_app_url(config.app_url)
    if getattr(args, "timeout_ms", None) is not None:
        base = max(args.timeout_ms, 1_000)
        config.login_timeout_ms = base
        config.nav_timeout_ms = base
        config.extract_timeout_ms = base
    return config


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    args = _build_parser().parse_args(argv)
    try:
        config = _resolve_config(args)
    except (ValueError, EOFError, KeyboardInterrupt) as exc:
        print(f"fiori-agent: bad config: {exc}", file=sys.stderr)
        return 2
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.command == "login":
            return cmd_login(config)
        if args.command == "inspect":
            return cmd_inspect(config)
        if args.command == "discover":
            return cmd_discover(config)
        if args.command == "ask-status":
            return cmd_ask_status(config, args.status)
        if args.command == "ask":
            return cmd_ask(config, args.question, route=args.route)
        if args.command == "report":
            return cmd_report(config)
        if args.command == "qa":
            return cmd_qa(config, no_color=args.no_color, fmt=args.format)
        if args.command == "agent":
            return cmd_agent(config, no_color=args.no_color)
    except KeyboardInterrupt:
        print("fiori-agent: interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        logger.error("command %s failed: %s: %s", args.command, type(exc).__name__, str(exc)[:300])
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
