"""Evaluation harness for the SAP Fiori agent (issue #649).

Runs each scenario from evals/scenarios.json as a real CLI subprocess against
the local PoC, scores stdout/stderr against the golden expectations, and prints
a verdict table with success metrics:
- verdict: per-scenario pass/fail
- answer_correct: golden answer/intent fields matched
- retries: consumed auth attempts beyond the first
- time_to_answer_ms: wall-clock duration of the scenario run
- report_completeness: expected markdown sections present + secret-clean

Exit code is nonzero when any scenario fails (CI-safe).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "evals" / "scenarios.json"
REPORT_PATH = ROOT / "artifacts" / "bug_report.md"
QA_REPORT_MD = ROOT / "artifacts" / "qa_report.md"
EVAL_RUNS_DIR = ROOT / "artifacts" / "eval_runs"

ATTEMPTS_RE = re.compile(r"attempts (\d+)")


@dataclass
class ScenarioResult:
    scenario_id: str
    kind: str
    passed: bool
    reason: str
    metrics: dict[str, Any] = field(default_factory=dict)


def load_scenarios() -> dict:
    with SCENARIOS.open() as fh:
        return json.load(fh)


def build_env(cfg: dict, scenario: dict) -> dict:
    env = dict(os.environ)
    env["SAP_AGENT_URL"] = cfg["app_url"]
    env["SAP_AGENT_USER"] = os.environ.get("SAP_AGENT_USER", cfg.get("username", "demo"))
    env["SAP_AGENT_PASSWORD"] = scenario.get(
        "password_override",
        os.environ.get("SAP_AGENT_PASSWORD", cfg.get("password", "")),
    )
    return env


def run_cli(cfg: dict, scenario: dict) -> tuple[str, str, int, float]:
    cmd_args = ["uv", "run", "python", "-m", "sap_agent.cli"]
    kind = scenario["kind"]
    if kind == "ask":
        cmd_args += ["ask", scenario["args"]["question"]]
        if scenario["args"].get("route"):
            cmd_args += ["--route", scenario["args"]["route"]]
    elif kind == "qa":
        cmd_args += ["qa", "--format", "json"]
    else:
        cmd_args.append(kind)
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd_args,
            cwd=ROOT,
            env=build_env(cfg, scenario),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        partial = ""
        if exc.stdout:
            partial = exc.stdout.decode() if isinstance(exc.stdout, bytes) else str(exc.stdout)
        return partial, f"timeout after 120s: {exc.cmd}", 124, elapsed_ms
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return proc.stdout, proc.stderr, proc.returncode, elapsed_ms


def retries_from_stderr(stderr: str) -> int:
    match = ATTEMPTS_RE.search(stderr)
    return max(int(match.group(1)) - 1, 0) if match else 0


def score_payload(payload: dict, expect: dict) -> tuple[bool, str]:
    for key, value in expect.items():
        if key in ("exit", "retries_max", "time_max_ms", "answer_groups_min", "answer_keys"):
            continue
        if key in ("answer", "intent"):
            if payload.get(key) != value:
                return False, f"{key}={payload.get(key)!r}, expected {value!r}"
        elif key in ("not_found", "unsupported") and payload.get(key) is not value:
            return False, f"{key}={payload.get(key)!r}, expected {value!r}"
    groups_min = expect.get("answer_groups_min")
    if groups_min is not None:
        answer = payload.get("answer")
        count = len(answer) if isinstance(answer, list) else 0
        if count < groups_min:
            return False, f"answer groups={count}, expected >= {groups_min}"
    required_keys = expect.get("answer_keys")
    if required_keys:
        answer = payload.get("answer")
        if not isinstance(answer, list) or not answer:
            return False, "answer is not a non-empty group list"
        first = answer[0] if isinstance(answer[0], dict) else {}
        missing = [k for k in required_keys if k not in first]
        if missing:
            return False, f"answer[0] missing keys {missing}"
    return True, "matches golden"


def check_gates(metrics: dict, expect: dict) -> tuple[bool, str]:
    """Budget gates applied after golden scoring: retries + wall-clock."""
    retries_max = expect.get("retries_max")
    if retries_max is not None and int(metrics.get("retries", 0)) > retries_max:
        return False, f"retries={metrics.get('retries')}, budget {retries_max}"
    time_max = expect.get("time_max_ms")
    if time_max is not None and int(metrics.get("time_to_answer_ms", 0)) > time_max:
        return False, f"ms={metrics.get('time_to_answer_ms')}, budget {time_max}"
    return True, ""


def validate_scenarios(cfg: dict) -> list[str]:
    """Static checks over scenarios.json: unique ids, required keys, known kinds."""
    errors: list[str] = []
    scenarios = cfg.get("scenarios", [])
    seen: set[str] = set()
    for idx, scenario in enumerate(scenarios):
        where = f"scenarios[{idx}]"
        sid = scenario.get("id")
        if not sid:
            errors.append(f"{where}: missing id")
            continue
        if sid in seen:
            errors.append(f"{where}: duplicate id {sid!r}")
        seen.add(sid)
        kind = scenario.get("kind")
        if kind not in {"login", "discover", "ask", "inspect", "report", "qa"}:
            errors.append(f"{sid}: unknown kind {kind!r}")
        if "expect" not in scenario:
            errors.append(f"{sid}: missing expect")
        if kind == "ask" and not scenario.get("args", {}).get("question"):
            errors.append(f"{sid}: ask scenario missing args.question")
    return errors


def score_discover(payload: dict, expect: dict) -> tuple[bool, str]:
    entities = {e["name"]: e for e in payload.get("entities", [])}
    for name in expect.get("has_entities", []):
        if name not in entities:
            return False, f"entity {name!r} missing"
    entity = entities.get(expect.get("has_entity"))
    if expect.get("has_entity") and entity is None:
        return False, f"entity {expect.get('has_entity')!r} missing"
    if entity is not None:
        tables = entity.get("tables", [])
        if not tables:
            return False, "entity has no tables"
        columns = tables[0].get("columns", [])
        missing = [c for c in expect.get("columns_superset", []) if c not in columns]
        if missing:
            return False, f"missing columns {missing}"
    min_tables = expect.get("tables_min")
    if min_tables is not None and len(payload.get("tables", [])) < min_tables:
        return False, f"tables={len(payload.get('tables', []))}, expected >= {min_tables}"
    return True, "summary matches golden"


def score_report(markdown: str, expect: dict, password: str) -> tuple[bool, str]:
    for section in expect.get("sections", []):
        if section not in markdown:
            return False, f"missing section {section!r}"
    if password and password in markdown:
        return False, "secret leaked into report"
    return True, "report complete and secret-clean"


def score_qa(payload: dict, expect: dict, report_md: str, password: str) -> tuple[bool, str]:
    pages = payload.get("pages", [])
    routes = {p.get("route") for p in pages}
    for route in expect.get("routes", []):
        if route not in routes:
            return False, f"route {route!r} not audited"
    pages_min = expect.get("pages_min")
    if pages_min is not None and len(pages) < pages_min:
        return False, f"pages={len(pages)}, expected >= {pages_min}"

    for source, floor_key in (("accessibility", "a11y_issues_min"), ("ux", "ux_issues_min")):
        floor = expect.get(floor_key)
        if floor is None:
            continue
        found = sum(len(p.get(f"{source}_issues", [])) for p in pages)
        if found < floor:
            return False, f"{source}_issues={found}, expected >= {floor}"

    severities_expected = expect.get("severities_present")
    if severities_expected is not None:
        found_severities = {
            issue.get("severity")
            for p in pages
            for issue in [*p.get("accessibility_issues", []), *p.get("ux_issues", [])]
        }
        missing = [s for s in severities_expected if s not in found_severities]
        if missing:
            return False, f"severities {missing} absent from findings"

    if expect.get("md_sections"):
        return score_report(report_md, {"sections": expect["md_sections"]}, password)
    return True, "qa report matches golden"


def score_scenario(
    kind: str,
    payload: dict | None,
    _stderr: str,
    report_md: str,
    expect: dict,
    password: str,
    exit_code: int,
) -> tuple[bool, str]:
    if kind == "login":
        if exit_code != expect.get("exit"):
            return False, f"exit={exit_code}, expected {expect.get('exit')}"
        return True, "login ok"
    if kind == "inspect":
        if exit_code != expect.get("exit"):
            return False, f"exit={exit_code}, expected {expect.get('exit')}"
        return True, "inspect ok"
    if kind == "discover":
        if exit_code != 0 or payload is None:
            return False, f"exit={exit_code}, payload missing"
        return score_discover(payload, expect)
    if kind == "ask":
        if exit_code != 0 or payload is None:
            return False, f"exit={exit_code}, payload missing"
        return score_payload(payload, expect)
    if kind == "report":
        if exit_code != expect.get("exit"):
            return False, f"exit={exit_code}, expected {expect.get('exit')}"
        return score_report(report_md, expect, password)
    if kind == "qa":
        if exit_code != 0 or payload is None:
            return False, f"exit={exit_code}, payload missing"
        return score_qa(payload, expect, report_md, password)
    return False, f"unknown kind {kind}"


def evaluate(cfg: dict, scenarios: list[dict]) -> list[ScenarioResult]:
    results: list[ScenarioResult] = []
    for scenario in scenarios:
        default_password = os.environ.get("SAP_AGENT_PASSWORD", cfg.get("password", ""))
        password = scenario.get("password_override", default_password)
        # Pre-clean report files so a stale artifact from a previous run
        # cannot false-pass report/qa scoring.
        if scenario["kind"] == "report":
            with contextlib.suppress(OSError):
                REPORT_PATH.unlink()
        if scenario["kind"] == "qa":
            with contextlib.suppress(OSError):
                QA_REPORT_MD.unlink()
        stdout, stderr, exit_code, elapsed_ms = run_cli(cfg, scenario)
        payload = None
        parse_error = ""
        try:
            payload = json.loads(stdout) if stdout.strip().startswith("{") else None
            if payload is None and stdout.strip():
                parse_error = f"stdout head: {stdout.strip()[:200]!r}"
        except json.JSONDecodeError as exc:
            parse_error = f"json error: {exc}"
            payload = None
        report_md = ""
        if scenario["kind"] == "report" and REPORT_PATH.exists():
            report_md = REPORT_PATH.read_text()
        if scenario["kind"] == "qa" and QA_REPORT_MD.exists():
            report_md = QA_REPORT_MD.read_text()
        passed, reason = score_scenario(
            scenario["kind"],
            payload,
            stderr,
            report_md,
            scenario["expect"],
            password,
            exit_code,
        )
        if not passed and parse_error and "payload missing" in reason:
            reason = f"{reason} ({parse_error})"
        metrics = {
            "exit": exit_code,
            "time_to_answer_ms": elapsed_ms,
            "retries": retries_from_stderr(stderr),
        }
        if passed:
            gate_ok, gate_reason = check_gates(metrics, scenario["expect"])
            if not gate_ok:
                passed, reason = False, gate_reason
        if scenario["kind"] == "ask":
            metrics["answer_correct"] = reason == "matches golden"
        if scenario["kind"] in ("report", "qa") and passed:
            metrics["report_completeness"] = True
        results.append(ScenarioResult(scenario["id"], scenario["kind"], passed, reason, metrics))
    return results


def print_table(results: list[ScenarioResult]) -> None:
    header = f"{'scenario':<24} {'kind':<10} {'verdict':<8} {'ms':>6} {'retries':>8}  reason"
    print(header)
    print("-" * len(header))
    for r in results:
        ms = r.metrics["time_to_answer_ms"]
        retries = r.metrics["retries"]
        print(f"{r.scenario_id:<24} {r.kind:<10} {'PASS' if r.passed else 'FAIL':<8} {ms:>6} {retries:>8}  {r.reason}")
    passed = sum(1 for r in results if r.passed)
    print("-" * len(header))
    print(f"{passed}/{len(results)} scenarios passed")


def git_sha() -> str:
    """Short SHA of the working tree, or 'unknown' outside a git repo."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except OSError:
        return "unknown"
    return proc.stdout.strip() or "unknown"


def persist_results(cfg: dict, results: list[ScenarioResult]) -> Path:
    """Write the run record to artifacts/eval_runs/<ts>.json and append to history.md."""
    EVAL_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    path = EVAL_RUNS_DIR / f"{stamp}.json"
    counter = 1
    while path.exists():
        counter += 1
        path = EVAL_RUNS_DIR / f"{stamp}-{counter}.json"

    passed = sum(1 for r in results if r.passed)
    times = [int(r.metrics.get("time_to_answer_ms", 0)) for r in results]
    total_ms = sum(times)
    avg_ms = round(total_ms / len(times)) if times else 0
    max_ms = max(times) if times else 0
    artifact_bytes = 0
    try:
        for p in ROOT.joinpath("artifacts").rglob("*"):
            if p.is_file() and EVAL_RUNS_DIR not in p.parents:
                artifact_bytes += p.stat().st_size
    except OSError:
        artifact_bytes = 0
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "agent_version": os.environ.get("SAP_AGENT_VERSION") or git_sha(),
        "app_url": cfg.get("app_url"),
        "python": sys.version.split()[0],
        "passed": passed,
        "total": len(results),
        "pass_rate": round(passed / len(results), 4) if results else 0.0,
        "total_ms": total_ms,
        "avg_ms": avg_ms,
        "max_ms": max_ms,
        "artifact_bytes": artifact_bytes,
        "results": [asdict(r) for r in results],
    }
    path.write_text(json.dumps(record, indent=2) + "\n")

    history = EVAL_RUNS_DIR / "history.md"
    header = "| timestamp | version | passed | total | pass_rate | avg_ms | max_ms |\n|---|---|---|---|---|---|---|\n"
    if not history.exists():
        history.write_text(header)
    else:
        first = history.read_text().splitlines(keepends=True)[:1]
        if first and "avg_ms" not in first[0]:
            body = history.read_text().split("\n", 2)
            history.write_text(header + "\n".join(body[2:]).lstrip("\n"))
    with history.open("a") as fh:
        fh.write(
            f"| {record['timestamp']} | {record['agent_version']} "
            f"| {record['passed']} | {record['total']} | {record['pass_rate']:.1%} "
            f"| {avg_ms} | {max_ms} |\n"
        )
    return path


def main() -> int:
    cfg = load_scenarios()
    errors = validate_scenarios(cfg)
    if errors:
        print("invalid scenarios.json:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 2
    results = evaluate(cfg, cfg["scenarios"])
    print_table(results)
    path = persist_results(cfg, results)
    print(f"run record written to {path.relative_to(ROOT)}")
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
