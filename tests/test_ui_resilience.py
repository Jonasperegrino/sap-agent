"""UI must survive a broken browser stack: engine failures degrade to inline
errors, never a full-page outage."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: prelude that makes ANY playwright import fail, simulating a broken/missing
#: browser dependency on the server.
BLOCK_PLAYWRIGHT = "import sys; sys.modules['playwright'] = None; sys.modules['playwright.sync_api'] = None; "


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", BLOCK_PLAYWRIGHT + code],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
    )


def test_service_importable_without_playwright() -> None:
    proc = _run("import sap_agent.ui.service as svc; print('import ok')")
    assert proc.returncode == 0, proc.stderr
    assert "import ok" in proc.stdout


def test_run_question_degrades_without_playwright() -> None:
    proc = _run(
        "from sap_agent.ui.service import run_question; "
        "from sap_agent.schemas import Config; "
        "cfg = Config(app_url='https://example.com', username='demo', password='x'); "
        "res = run_question(cfg, 'hi'); "
        "assert res.answer is None, res; "
        "assert res.error, 'expected an error result, got success'; "
        "print('error:', res.error[:80])"
    )
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr


def test_streamlit_app_boots_without_playwright() -> None:
    proc = _run("import runpy; runpy.run_path('streamlit_app.py', run_name='__main__'); print('boot ok')")
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr
    assert "boot ok" in proc.stdout


def test_demo_case_and_ui_markup_load_without_playwright() -> None:
    # The scripted investigation must keep working when the browser stack is down.
    proc = _run(
        "from sap_agent import demo_case as d; from sap_agent.ui import components as ui; "
        "r = d.analyze_demo_ingestion(); "
        "assert d.matches_demo_question(d.QUESTION); "
        "assert 'normalization skipped' in ui.evidence_html(d.trace_rows(r)); "
        "print('demo ok')"
    )
    assert proc.returncode == 0, proc.stderr
    assert "demo ok" in proc.stdout
