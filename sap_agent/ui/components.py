"""HTML building blocks for the Atlas operator UI.

Pure string builders (no Streamlit import) so the markup is unit-testable and
the page script stays declarative. Every dynamic value is HTML-escaped here.
The stylesheet is scoped to ``atlas-`` classes plus a few layout overrides.
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from collections.abc import Sequence


class Row(TypedDict):
    label: str
    value: str
    tone: str


class Chip(TypedDict):
    text: str
    tone: str  # "neutral" | "amber" | "red" | "blue"


STYLE = """
<style>
:root {
  --atlas-ink: #0e1a2b;
  --atlas-text: #31333f;
  --atlas-soft: #5c6475;
  --atlas-line: #e3e7ee;
  --atlas-blue: #1c6fd1;
  --atlas-ok: #21a366;
  --atlas-bad: #d81f3a;
}
/* The page reads like one product window, a little wider than Streamlit's default column. */
[data-testid="stMainBlockContainer"] { max-width: 62rem; padding-top: 3.5rem; }

/* The primary button keeps its colour after a click; keyboard focus still gets a visible ring. */
[data-testid="stBaseButton-primary"] { font-weight: 600; }
[data-testid="stBaseButton-primary"]:focus:not(:active):not(:hover) {
  background-color: #0b66c3; border-color: #0b66c3;
}
[data-testid="stBaseButton-primary"]:focus-visible { outline: 2px solid var(--atlas-ink); outline-offset: 2px; }

.atlas-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }
.atlas-head h1 {
  margin: 0; padding: 0.4rem 0 0.2rem;
  font-size: 3.5rem; line-height: 1.1; font-weight: 700; letter-spacing: -0.01em; color: var(--atlas-ink);
}
.atlas-pill {
  display: inline-flex; align-items: center; gap: 0.375rem; flex-shrink: 0;
  padding: 0.25rem 0.625rem; border-radius: 0.375rem;
  background: rgba(28, 111, 209, 0.09); color: #1a66c2; font-size: 1rem; white-space: nowrap;
}
.atlas-pill .atlas-ico-ok { width: 0.95rem; height: 0.95rem; background: none; border: 1.5px solid var(--atlas-blue); }
.atlas-pill .atlas-ico-ok::after { border-color: var(--atlas-blue); border-width: 0 1.5px 1.5px 0; }

/* Status icons are drawn in CSS: a ring that spins while running, a green check when done. */
.atlas-ico-run, .atlas-ico-ok {
  display: inline-block; position: relative; flex-shrink: 0; box-sizing: border-box;
  width: 1.65rem; height: 1.65rem; border-radius: 50%;
}
.atlas-ico-run { border: 3px solid var(--atlas-line); border-top-color: var(--atlas-blue); }
.atlas-ico-ok { background: var(--atlas-ok); }
.atlas-ico-ok::after {
  content: ""; position: absolute; left: 50%; top: 46%; width: 28%; height: 52%;
  border: solid #ffffff; border-width: 0 2.5px 2.5px 0; transform: translate(-50%, -50%) rotate(45deg);
}

.atlas-panel {
  position: relative; border: 1.5px solid var(--atlas-line); border-radius: 0.625rem;
  background: #ffffff; color: var(--atlas-text);
}
.atlas-status { display: flex; align-items: center; gap: 0.75rem; padding: 0.8rem 1.1rem; font-size: 1.05rem; }
.atlas-status .atlas-right { margin-left: auto; font-weight: 600; color: var(--atlas-ink); text-align: right; }
.atlas-ico-run { animation: atlas-spin 0.9s linear infinite; }

.atlas-evidence { padding: 0.8rem 1.1rem 0.9rem; }
.atlas-evidence h2, .atlas-evidence h3, .atlas-evidence h4 {
  margin: 0 0 0.55rem; padding: 0; font-size: 1rem; line-height: 1.4; font-weight: 600; color: var(--atlas-text);
}
.atlas-rows { margin: 0; padding: 0; }
.atlas-rows.is-filling { min-height: 7.4rem; }
.atlas-row {
  display: flex; justify-content: space-between; gap: 1.5rem; margin: 0; padding: 0.2rem 0;
  font-family: "Source Code Pro", ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.94rem; line-height: 1.5;
}
.atlas-row .k { color: var(--atlas-soft); }
.atlas-row .v { color: var(--atlas-ink); font-weight: 500; text-align: right; white-space: nowrap; }
.atlas-row .v.is-bad { color: var(--atlas-bad); }
.atlas-in { animation: atlas-in 0.4s cubic-bezier(0.22, 1, 0.36, 1) both; }

.atlas-answer { margin: 0.4rem 0 0; }
.atlas-answer h2 {
  margin: 0; padding: 0.6rem 0 0.2rem;
  font-size: 2.1rem; line-height: 1.15; font-weight: 600; letter-spacing: -0.02em; color: var(--atlas-ink);
}
.atlas-answer p { margin: 0.2rem 0 0.8rem; font-size: 1.05rem; line-height: 1.5; color: var(--atlas-text); }
.atlas-chips { display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0; padding: 0; }
.atlas-chip {
  margin: 0; padding: 0.3rem 0.7rem; border-radius: 0.5rem;
  font-size: 0.95rem; font-weight: 500; white-space: nowrap; background: #eef3f9; color: #14223a;
}
.atlas-chip.is-amber { background: #fff4d6; color: #7a5000; }
.atlas-chip.is-red { background: #ffe6ea; color: #b01028; }
.atlas-chip.is-blue { background: #e4f0fb; color: #00509c; }
.atlas-rec {
  display: inline-block; margin: 0.15rem 0; padding: 0.45rem 0.8rem; border-radius: 0.5rem;
  background: #fff9e3; color: #6b4b00; font-size: 0.98rem;
}
.atlas-note { margin: 0.2rem 0 0; font-size: 0.85rem; line-height: 1.45; color: var(--atlas-soft); }
.atlas-empty { padding: 1.4rem 1.2rem; text-align: center; color: var(--atlas-soft); }
.atlas-empty strong { display: block; margin-bottom: 0.25rem; color: var(--atlas-ink); font-size: 1.05rem; }

@keyframes atlas-spin { to { transform: rotate(360deg); } }
@keyframes atlas-in { from { opacity: 0; transform: translateX(1.5rem); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) {
  .atlas-ico-run, .atlas-in { animation: none; }
}
@media (forced-colors: active) {
  .atlas-panel, .atlas-chip, .atlas-pill, .atlas-rec { border: 1px solid CanvasText; }
  .atlas-row .v.is-bad { color: CanvasText; text-decoration: underline; }
  .atlas-ico-ok, .atlas-ico-run { forced-color-adjust: none; }
}
@media (max-width: 640px) {
  .atlas-head { flex-direction: column-reverse; gap: 0.25rem; }
  .atlas-head h1 { font-size: 2.4rem; }
  .atlas-row { flex-direction: column; gap: 0; padding: 0.35rem 0; }
  .atlas-row .v { text-align: left; white-space: normal; }
  .atlas-status { flex-wrap: wrap; }
  .atlas-status .atlas-right { margin-left: 2.4rem; text-align: left; }
}
</style>
"""

_CHECK = '<span class="atlas-ico-ok" aria-hidden="true"></span>'
_RING = '<span class="atlas-ico-run" aria-hidden="true"></span>'


def header_html(title: str, pill: str) -> str:
    """Page title with the trust pill at the top right."""
    return (
        f'<div class="atlas-head"><h1>{escape(title)}</h1><span class="atlas-pill">{_CHECK}{escape(pill)}</span></div>'
    )


def status_html(label: str, *, running: bool, right: str = "", animate: bool = False) -> str:
    """One-line status panel: a spinner while running, a green check when done."""
    icon = _RING if running else _CHECK
    tail = f'<span class="atlas-right">{escape(right)}</span>' if right else ""
    cls = "atlas-panel atlas-status atlas-in" if animate else "atlas-panel atlas-status"
    return f'<div class="{cls}" role="status">{icon}<span>{escape(label)}</span>{tail}</div>'


def evidence_html(
    rows: Sequence[Row],
    *,
    title: str = "Evidence and trace",
    newest: int | None = None,
    heading: str = "h2",
) -> str:
    """Label/value trace. ``newest`` marks the row that just arrived (it slides in)."""
    tag = heading if heading in ("h2", "h3", "h4") else "h2"
    items = []
    for index, row in enumerate(rows):
        cls = "atlas-row atlas-in" if index == newest else "atlas-row"
        value_cls = "v is-bad" if row["tone"] == "bad" else "v"
        items.append(
            f'<div class="{cls}"><span class="k">{escape(row["label"])}</span>'
            f'<span class="{value_cls}">{escape(row["value"])}</span></div>'
        )
    rows_cls = "atlas-rows is-filling" if newest is not None or not rows else "atlas-rows"
    return (
        f'<div class="atlas-panel atlas-evidence"><{tag}>{escape(title)}</{tag}>'
        f'<div class="{rows_cls}">{"".join(items)}</div></div>'
    )


def answer_html(headline: str, lead: str, chips: Sequence[Chip]) -> str:
    """Result summary: what was found, in one sentence, with its key facts as chips."""
    chip_items = "".join(
        f'<span class="atlas-chip{"" if chip["tone"] == "neutral" else " is-" + escape(chip["tone"])}">'
        f"{escape(chip['text'])}</span>"
        for chip in chips
    )
    lead_html = f"<p>{escape(lead)}</p>" if lead else ""
    return (
        f'<div class="atlas-answer"><h2>{escape(headline)}</h2>{lead_html}'
        f'<div class="atlas-chips">{chip_items}</div></div>'
    )


def recommendation_html(text: str) -> str:
    """The recommended next step, as one highlighted line."""
    return f'<p class="atlas-rec">{escape(text)}</p>'


def note_html(text: str) -> str:
    """Small print under a result, e.g. where the figures come from."""
    return f'<p class="atlas-note">{escape(text)}</p>'


def empty_html(title: str, text: str) -> str:
    """Empty state for a tab that has nothing to show yet."""
    return f'<div class="atlas-panel atlas-empty"><strong>{escape(title)}</strong>{escape(text)}</div>'
