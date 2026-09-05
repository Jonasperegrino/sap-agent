"""Gate tests for the hardened eval harness (no browser).

Covers the B3 additions: aggregate structural goldens, retry/latency gates,
scenario validation, and timeout handling in run_cli.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.run_eval import (
    check_gates,
    run_cli,
    score_payload,
    validate_scenarios,
)


class TestAggregateGoldens:
    def test_groups_min_pass(self) -> None:
        payload = {"intent": "aggregate", "answer": [{"status": "A", "count": 2}, {"status": "B", "count": 1}]}
        passed, _ = score_payload(payload, {"intent": "aggregate", "answer_groups_min": 2})
        assert passed

    def test_groups_min_fail(self) -> None:
        payload = {"intent": "aggregate", "answer": [{"status": "A", "count": 2}]}
        passed, reason = score_payload(payload, {"intent": "aggregate", "answer_groups_min": 3})
        assert not passed
        assert "groups=" in reason

    def test_answer_keys_pass(self) -> None:
        payload = {"intent": "aggregate", "answer": [{"category": "M", "count": 4}]}
        passed, _ = score_payload(payload, {"answer_keys": ["category", "count"]})
        assert passed

    def test_answer_keys_missing(self) -> None:
        payload = {"intent": "aggregate", "answer": [{"category": "M", "count": 4}]}
        passed, reason = score_payload(payload, {"answer_keys": ["category", "average"]})
        assert not passed
        assert "missing keys" in reason

    def test_answer_not_a_list(self) -> None:
        passed, _ = score_payload({"intent": "aggregate", "answer": 5}, {"answer_keys": ["average"]})
        assert not passed

    def test_gate_keys_ignored_by_golden(self) -> None:
        payload = {"intent": "aggregate", "answer": [{"average": 1.5}]}
        passed, reason = score_payload(
            payload, {"intent": "aggregate", "answer_groups_min": 1, "answer_keys": ["average"]}
        )
        assert passed
        assert reason == "matches golden"


class TestCheckGates:
    def test_retries_within_budget(self) -> None:
        assert check_gates({"retries": 1, "time_to_answer_ms": 100}, {"retries_max": 2}) == (True, "")

    def test_retries_over_budget(self) -> None:
        ok, reason = check_gates({"retries": 3, "time_to_answer_ms": 100}, {"retries_max": 2})
        assert not ok
        assert "retries=3" in reason

    def test_latency_over_budget(self) -> None:
        ok, reason = check_gates({"retries": 0, "time_to_answer_ms": 5_000}, {"time_max_ms": 1_000})
        assert not ok
        assert "ms=5000" in reason

    def test_no_gates_always_pass(self) -> None:
        assert check_gates({"retries": 9, "time_to_answer_ms": 99_999}, {}) == (True, "")


class TestValidateScenarios:
    def test_valid(self) -> None:
        cfg = {
            "scenarios": [
                {"id": "a", "kind": "ask", "args": {"question": "q?"}, "expect": {}},
                {"id": "b", "kind": "login", "expect": {"exit": 0}},
            ]
        }
        assert validate_scenarios(cfg) == []

    def test_duplicate_ids(self) -> None:
        cfg = {"scenarios": [{"id": "a", "kind": "login", "expect": {}}, {"id": "a", "kind": "login", "expect": {}}]}
        errors = validate_scenarios(cfg)
        assert any("duplicate" in e for e in errors)

    def test_unknown_kind_and_missing_question(self) -> None:
        cfg = {"scenarios": [{"id": "x", "kind": "nope", "expect": {}}, {"id": "y", "kind": "ask", "expect": {}}]}
        errors = validate_scenarios(cfg)
        assert any("unknown kind" in e for e in errors)
        assert any("args.question" in e for e in errors)


class TestRunCliTimeout:
    def test_timeout_becomes_failed_run(self) -> None:
        import subprocess

        with patch("evals.run_eval.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 120)):
            stdout, stderr, exit_code, elapsed = run_cli(
                {"app_url": "http://x"}, {"id": "t", "kind": "login", "expect": {}}
            )
        assert exit_code == 124
        assert "timeout" in stderr
        assert elapsed >= 0
        assert stdout == ""
