"""Internal tools for the Fiori agent (architecture D4)."""

from .accessibility import audit_accessibility
from .answer import evaluate_question
from .auth import AuthError, AuthResult, login, validate_app_url
from .discover import discover_app
from .extract import TableData, get_all_tables, get_table_data
from .network import NetworkCapture
from .qa import run_qa, write_qa_report
from .reason import parse_question
from .report import classify_failure, collect_artifacts, should_retry, write_report
from .screenshot import capture_page
from .ux_critique import critique_ux

__all__ = [
    "AuthError",
    "AuthResult",
    "login",
    "validate_app_url",
    "TableData",
    "get_all_tables",
    "get_table_data",
    "NetworkCapture",
    "evaluate_question",
    "parse_question",
    "discover_app",
    "classify_failure",
    "collect_artifacts",
    "should_retry",
    "write_report",
    "capture_page",
    "audit_accessibility",
    "critique_ux",
    "run_qa",
    "write_qa_report",
]
