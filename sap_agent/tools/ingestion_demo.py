"""Backward-compatible import path for the seeded SO-1024 demo case.

The case lives in ``sap_agent.demo_case`` so the operator UI can load it
without importing this package (whose ``__init__`` pulls in the browser stack).
"""

from __future__ import annotations

from ..demo_case import (
    CASE,
    QUESTION,
    BackendFinding,
    DemoCase,
    DemoResult,
    Finding,
    TraceRow,
    analyze_demo_ingestion,
    matches_demo_question,
    report_markdown,
    trace_rows,
)

__all__ = [
    "CASE",
    "QUESTION",
    "BackendFinding",
    "DemoCase",
    "DemoResult",
    "Finding",
    "TraceRow",
    "analyze_demo_ingestion",
    "matches_demo_question",
    "report_markdown",
    "trace_rows",
]
