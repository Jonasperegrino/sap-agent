"""The generated regression test is valid Python and carries the investigation's figures."""

from __future__ import annotations

import ast

from sap_agent.demo_case import CASE, analyze_demo_ingestion
from sap_agent.ui.regression import regression_test_source


def test_generated_source_parses_as_python() -> None:
    ast.parse(regression_test_source())


def test_generated_source_uses_case_figures() -> None:
    source = regression_test_source(analyze_demo_ingestion())
    assert f"€{CASE['order_rows_sum_eur']:,.2f}" in source
    assert f"€{CASE['dashboard_total_eur']:,.2f}" in source
    assert CASE["order_id"] in source


def test_generated_source_defines_one_test() -> None:
    tree = ast.parse(regression_test_source())
    names = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    assert names == ["test_total_value_matches_order_amounts"]
