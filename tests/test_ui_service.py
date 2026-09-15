"""Unit tests for Streamlit-facing UI service helpers."""

from __future__ import annotations

from sap_agent.schemas import Config, FailureClass
from sap_agent.ui.service import report_nonfunctional_button


def test_report_nonfunctional_button_writes_product_bug(tmp_path) -> None:
    config = Config(app_url="http://localhost:8501", artifacts_dir=str(tmp_path))

    result = report_nonfunctional_button(config, "Open Forecast Panel")

    assert result.report is not None
    assert result.report.classification is FailureClass.PRODUCT_BUG
    assert "no visible state change" in result.report.actual
    assert result.report_path is not None
    assert result.report_path.exists()
    text = result.report_path.read_text()
    assert "Non-functional button detected" in text
    assert "Open Forecast Panel" in text