"""Gap tests for infra paths: extract fast path, network headers, QA persist."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fakes import FakeResponse, ScriptedEvaluatePage
from test_network import EmittingPage

from sap_agent.context import SessionContext
from sap_agent.schemas import Config, QaPageReport, QaReport
from sap_agent.tools.extract import _extract_via_evaluate
from sap_agent.tools.network import NetworkCapture


def test_extract_via_evaluate_tables() -> None:
    page = ScriptedEvaluatePage(results=[[{"columns": ["A", "B"], "rows": [["1", "2"]], "row_count": 1}]])
    tables = _extract_via_evaluate(page, 100)
    assert tables is not None
    assert tables[0].columns == ["A", "B"]
    assert tables[0].row_count == 1


def test_extract_via_evaluate_non_list() -> None:
    assert _extract_via_evaluate(ScriptedEvaluatePage(results=[{"nope": 1}]), 100) is None


def test_extract_via_evaluate_non_dict_entry() -> None:
    assert _extract_via_evaluate(ScriptedEvaluatePage(results=[[42]]), 100) is None


class HeaderResponse(FakeResponse):
    def __init__(self, url: str, body: object, headers: dict) -> None:
        super().__init__(url, body, headers)


def test_non_json_content_type_skipped() -> None:
    page = EmittingPage([])
    capture = NetworkCapture(page, "http://localhost:8080")
    resp = HeaderResponse("http://localhost:8080/app.js", {}, {"content-type": "application/javascript"})
    capture._on_response(resp)
    assert capture.capture_response_urls() == ["http://localhost:8080/app.js"]
    assert capture.latest_response_body("app.js") is None


def test_oversize_body_skipped() -> None:
    page = EmittingPage([])
    capture = NetworkCapture(page, "http://localhost:8080")
    resp = HeaderResponse(
        "http://localhost:8080/data/big.json",
        [{"a": 1}],
        {"content-type": "application/json", "content-length": "99999999"},
    )
    capture._on_response(resp)
    assert capture.capture_response_urls() == ["http://localhost:8080/data/big.json"]
    assert capture.latest_response_body("big.json") is None


def test_json_content_type_kept() -> None:
    page = EmittingPage([])
    capture = NetworkCapture(page, "http://localhost:8080")
    resp = HeaderResponse("http://localhost:8080/data/sales.json", [{"a": 1}], {"content-type": "application/json"})
    capture._on_response(resp)
    assert capture.latest_response_body("sales.json") == [{"a": 1}]


def _report() -> QaReport:
    return QaReport(
        app_url="http://x",
        generated_at="2026-01-01T00:00:00",
        pages=[QaPageReport(route="dashboard")],
    )


def _ctx(tmp_path: Path) -> SessionContext:
    cfg = Config(app_url="http://x", username="u", password="p", artifacts_dir=str(tmp_path))
    return SessionContext(cfg)


def test_persist_and_emit_json(tmp_path: Path) -> None:
    from sap_agent.cli_commands import _persist_and_emit_qa

    ctx = _ctx(tmp_path)
    path = _persist_and_emit_qa(_report(), ctx, fmt="json")
    assert Path(path).exists()
    assert (tmp_path / "qa_report.md").exists()


def test_persist_and_emit_markdown(tmp_path: Path) -> None:
    from sap_agent.cli_commands import _persist_and_emit_qa

    ctx = _ctx(tmp_path)
    path = _persist_and_emit_qa(_report(), ctx, fmt="markdown")
    assert Path(path).exists()


def test_persist_and_emit_terminal(tmp_path: Path, capsys) -> None:
    from sap_agent.cli_commands import _persist_and_emit_qa

    ctx = _ctx(tmp_path)
    path = _persist_and_emit_qa(_report(), ctx, fmt=None)
    assert Path(path).exists()
    out = capsys.readouterr().out
    assert "0 issues" in out


def test_persist_diff_vs_previous(tmp_path: Path) -> None:
    from sap_agent.cli_commands import _persist_and_emit_qa

    ctx = _ctx(tmp_path)
    _persist_and_emit_qa(_report(), ctx, fmt="json")
    # second run diffs against history
    path = _persist_and_emit_qa(_report(), ctx, fmt="json")
    assert Path(path).exists()


def test_controller_step_exception_is_failure() -> None:
    from fakes import PageStub

    from sap_agent.controller import AgentLoop

    class BoomPage(PageStub):
        pass

    config = Config(app_url="http://x", username="u", password="p")
    ctx = SessionContext(config)
    loop = AgentLoop(config, BoomPage(), ctx)

    def step(page, ctx):
        raise ValueError("rogue")

    result = loop._run_step(step)
    assert result.status.value == "failure"
    assert result.transient is False
    assert "ValueError" in result.detail


def test_post_json_posts_and_parses() -> None:
    from sap_agent.tools.llm import _post_json

    class Resp:
        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch("sap_agent.tools.llm.urllib.request.urlopen", return_value=Resp()):
        assert _post_json("http://x", {}, {"a": 1}, 5.0) == {"ok": True}


def test_trace_lines_serialize() -> None:
    ctx = SessionContext(Config(app_url="http://x", username="u", password="p"))
    ctx.record("tool", "action", "outcome")
    lines = ctx.trace_lines()
    assert len(lines) == 1
    assert '"action"' in lines[0]
