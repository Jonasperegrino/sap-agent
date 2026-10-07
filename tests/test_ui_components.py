"""Markup builders for the operator UI: escaping, tones and the row-by-row reveal."""

from __future__ import annotations

from sap_agent.demo_case import analyze_demo_ingestion, trace_rows
from sap_agent.ui import components as ui


def test_trace_rows_tell_the_story_in_order() -> None:
    rows = trace_rows(analyze_demo_ingestion())
    assert [(r["label"], r["value"]) for r in rows] == [
        ("Fiori dashboard · Total Value", "€194,956.50"),
        ("Order rows · sum of Amount", "€173,956.50"),
        ("SO-1024 · row €4,000.00 · amountEur", "€25,000.00"),
        ("BRL 25,000.00 × 0.16 · non_eu_v2", "normalization skipped"),
    ]
    # Only the cause is flagged, and only the ingestion line is simulated.
    assert [r["tone"] for r in rows] == ["plain", "plain", "plain", "bad"]
    assert [r["source"] for r in rows] == ["fiori", "fiori", "fiori", "ingestion"]


def test_evidence_marks_only_the_newest_row_and_the_bad_value() -> None:
    rows = trace_rows()
    html = ui.evidence_html(rows[:2], newest=1)
    assert html.count("atlas-row atlas-in") == 1
    assert "is-filling" in html
    done = ui.evidence_html(rows)
    assert "atlas-in" not in done and "is-filling" not in done
    assert done.count('class="v is-bad"') == 1
    assert "<h2>Evidence and trace</h2>" in done
    assert "<h4>" in ui.evidence_html(rows, heading="h4")


def test_status_switches_icon_and_keeps_the_right_hand_text() -> None:
    running = ui.status_html("Running agent…", running=True)
    done = ui.status_html("Bug report ready", running=False, right="SO-1024 · Currency mapping defect")
    assert "atlas-ico-run" in running and "atlas-ico-ok" not in running
    assert "atlas-ico-ok" in done and "SO-1024 · Currency mapping defect" in done
    assert 'role="status"' in running


def test_dynamic_text_is_escaped() -> None:
    nasty = '<img src=x onerror="alert(1)">'
    for html in (
        ui.header_html(nasty, nasty),
        ui.status_html(nasty, running=False, right=nasty),
        ui.evidence_html([{"label": nasty, "value": nasty, "tone": "plain"}]),
        ui.answer_html(nasty, nasty, [{"text": nasty, "tone": "amber"}]),
        ui.recommendation_html(nasty),
        ui.note_html(nasty),
        ui.empty_html(nasty, nasty),
    ):
        assert "<img" not in html
        assert "&lt;img" in html


def test_stylesheet_has_reduced_motion_and_forced_colors_fallbacks() -> None:
    assert "prefers-reduced-motion: reduce" in ui.STYLE
    assert "forced-colors: active" in ui.STYLE
