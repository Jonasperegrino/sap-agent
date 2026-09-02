"""Unit tests for the evaluation harness scoring (no browser, issue #649)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.run_eval import (  # noqa: E402
    build_env,
    load_scenarios,
    retries_from_stderr,
    score_discover,
    score_payload,
    score_report,
    score_scenario,
)


class TestScorePayload:
    def test_answer_match(self) -> None:
        passed, reason = score_payload({"answer": "2"}, {"exit": 0, "answer": "2"})
        assert passed
        assert reason == "matches golden"

    def test_answer_mismatch(self) -> None:
        passed, reason = score_payload({"answer": "3"}, {"answer": "2"})
        assert not passed
        assert "answer" in reason

    def test_not_found_flag(self) -> None:
        passed, _ = score_payload({"not_found": True}, {"not_found": True})
        assert passed
        passed, _ = score_payload({"not_found": False}, {"not_found": True})
        assert not passed

    def test_unsupported_flag(self) -> None:
        passed, _ = score_payload({"unsupported": True}, {"unsupported": True})
        assert passed

    def test_intent_match(self) -> None:
        passed, _ = score_payload({"intent": "count_where"}, {"intent": "count_where"})
        assert passed


class TestScoreDiscover:
    def test_entity_and_columns_present(self) -> None:
        payload = {
            "entities": [
                {
                    "name": "sales",
                    "tables": [{"name": "salesTable", "columns": ["id", "customer", "amount", "status", "built"]}],
                }
            ]
        }
        expect = {"has_entity": "sales", "columns_superset": ["customer", "amount", "status", "built"]}
        passed, reason = score_discover(payload, expect)
        assert passed
        assert reason == "summary matches golden"

    def test_missing_entity(self) -> None:
        payload = {"entities": [{"name": "other", "tables": [{"name": "t", "columns": ["a"]}]}]}
        passed, reason = score_discover(payload, {"has_entity": "sales", "columns_superset": []})
        assert not passed
        assert "missing" in reason

    def test_missing_column(self) -> None:
        payload = {"entities": [{"name": "sales", "tables": [{"name": "t", "columns": ["id"]}]}]}
        passed, reason = score_discover(payload, {"has_entity": "sales", "columns_superset": ["built"]})
        assert not passed
        assert "columns" in reason


class TestScoreReport:
    def test_sections_present_and_secret_clean(self) -> None:
        md = "# bug\n## Expected\n## Actual\n## Reproduction steps\n## Environment\n## Attachments\n"
        passed, reason = score_report(md, {"sections": ["## Expected", "## Attachments"]}, "hunter2")
        assert passed
        assert reason == "report complete and secret-clean"

    def test_missing_section(self) -> None:
        md = "## Expected\n## Attachments\n"
        passed, reason = score_report(md, {"sections": ["## Environment"]}, "hunter2")
        assert not passed
        assert "Environment" in reason

    def test_secret_leak(self) -> None:
        md = "## Expected\npassword:hunter2\n## Attachments\n"
        passed, reason = score_report(md, {"sections": []}, "hunter2")
        assert not passed
        assert "secret" in reason


class TestRetriesFromStderr:
    def test_parses_attempts(self) -> None:
        assert retries_from_stderr("login failed (bad_credentials, attempts 3)") == 2

    def test_no_attempts_means_zero(self) -> None:
        assert retries_from_stderr("nothing here") == 0


class TestLoadScenarios:
    def test_expected_scenarios_present(self) -> None:
        cfg = load_scenarios()
        ids = {s["id"] for s in cfg["scenarios"]}
        assert {"login-good", "discover-summary", "ask-status-approved", "report-bad-creds"} <= ids

    def test_app_config_present(self) -> None:
        cfg = load_scenarios()
        assert cfg["app_url"] == "http://localhost:8080"
        # Credentials come from env vars (SAP_AGENT_USER / SAP_AGENT_PASSWORD),
        # not from scenarios.json — no hardcoded passwords in the repo.
        assert "password" not in cfg


class TestScoreScenario:
    def test_login_exit_mismatch(self) -> None:
        passed, _ = score_scenario("login", None, "", "", {"exit": 1}, "pw", 0)
        assert not passed

    def test_report_exit_and_sections(self) -> None:
        md = "## Expected\n## Actual\n"
        passed, _ = score_scenario("report", None, "", md, {"exit": 1, "sections": ["## Actual"]}, "pw", 1)
        assert passed

    def test_unknown_kind_fails(self) -> None:
        passed, reason = score_scenario("bogus", None, "", "", {}, "pw", 0)
        assert not passed
        assert "unknown kind" in reason


class TestBuildEnv:
    def test_uses_env_var_over_cfg(self) -> None:
        cfg = {"app_url": "http://x", "username": "cfg_user", "password": "cfg_pass"}
        scenario = {}
        env = build_env(cfg, scenario)
        assert env["SAP_AGENT_URL"] == "http://x"

    def test_password_override_wins(self) -> None:
        cfg = {"app_url": "http://x", "password": "real"}
        scenario = {"password_override": "wrong"}
        env = build_env(cfg, scenario)
        assert env["SAP_AGENT_PASSWORD"] == "wrong"

    def test_cfg_password_used_when_no_override(self) -> None:
        cfg = {"app_url": "http://x", "password": "real"}
        scenario = {}
        env = build_env(cfg, scenario)
        assert env["SAP_AGENT_PASSWORD"] == "real"


class TestPersistResults:
    def test_persists_record_and_history(self, tmp_path, monkeypatch) -> None:
        import evals.run_eval as run_eval

        monkeypatch.setattr(run_eval, "EVAL_RUNS_DIR", tmp_path)
        cfg = {"app_url": "http://x"}
        results = [
            run_eval.ScenarioResult("ask-count-approved", "ask", True, "matches golden", {"exit": 0}),
            run_eval.ScenarioResult("login-bad-password", "login", False, "exit=1", {"exit": 1}),
        ]

        path = run_eval.persist_results(cfg, results)

        record = json.loads(path.read_text())
        assert record["app_url"] == "http://x"
        assert record["passed"] == 1
        assert record["total"] == 2
        assert record["pass_rate"] == 0.5
        assert record["results"][0]["scenario_id"] == "ask-count-approved"
        assert record["results"][1]["metrics"]["exit"] == 1

        history = (tmp_path / "history.md").read_text()
        assert "| timestamp | version | passed | total | pass_rate |" in history
        assert "| 50.0% |" in history
