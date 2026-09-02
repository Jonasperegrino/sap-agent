"""UX critique tool (issue #688): deterministic visual-quality heuristics.

Computed-style checks run in-browser against the visible UI5 page: spacing
consistency of repeated controls, horizontal alignment of sibling elements,
visual hierarchy (page title vs body text), and interaction affordances
(cursor, disabled styling). Cross-page consistency is assembled by the QA
orchestrator (`tools/qa.py`), which can compare pages.
"""

from __future__ import annotations

from playwright.sync_api import Page

from ..schemas import Severity, UxIssue

_VISIBLE_PAGE = ".sapMPage:visible"

_CRITIQUE_SCRIPT = """
() => {
  const page = Array.from(document.querySelectorAll('.sapMPage'))
    .find((p) => p.getClientRects().length > 0 &&
      getComputedStyle(p).display !== 'none' &&
      getComputedStyle(p).visibility !== 'hidden') || document.body;
  const issues = [];

  const describe = (el) => {
    const tag = el.tagName.toLowerCase();
    const id = el.id ? '#' + el.id : '';
    const cls = typeof el.className === 'string' && el.className
      ? '.' + el.className.split(/\\s+/).slice(0, 2).join('.') : '';
    return ('<' + tag + id + cls + '>').slice(0, 120);
  };

  const visible = (el) => el.offsetParent !== null ||
    (el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden');

  const buttons = Array.from(page.querySelectorAll('.sapMBtn, button')).filter(visible);
  const paddings = buttons
    .map((b) => getComputedStyle(b).paddingLeft)
    .filter((p) => p && p !== '0px');
  if (paddings.length >= 3) {
    const px = (v) => parseFloat(v);
    const vals = paddings.map(px);
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    if (min > 0 && (max - min) / min > 0.5) {
      issues.push({type: 'spacing_inconsistency', element: `buttons (n=${buttons.length})`,
        severity: 'low',
        suggestion: `button horizontal padding varies ${min}px..${max}px — use one spacing scale`});
    }
  }

  const rowsByTop = new Map();
  page.querySelectorAll('.sapMTitle, .sapMText, .sapMObjectTitle, label, td').forEach((el) => {
    if (!visible(el) || !el.textContent.trim()) return;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    const top = Math.round(rect.top / 4) * 4;
    if (!rowsByTop.has(top)) rowsByTop.set(top, []);
    rowsByTop.get(top).push({el, left: rect.left});
  });
  rowsByTop.forEach((row) => {
    if (row.length < 2) return;
    const lefts = row.map((r) => Math.round(r.left));
    const [a, b] = [Math.min(...lefts), Math.max(...lefts)];
    if (b - a > 2 && b - a < 300) {
      const graded = row.filter((r) => Math.abs(Math.round(r.left) - b) <= 2);
      if (graded.length >= 1 && row.length - graded.length >= 1) {
        issues.push({type: 'alignment_issue', element: describe(graded[0].el),
          severity: 'low',
          suggestion: `element is ${b - a}px off the row's dominant left edge`});
      }
    }
  });

  const titles = Array.from(page.querySelectorAll('.sapMTitle')).filter(visible);
  const bodyFonts = Array.from(page.querySelectorAll('.sapMText, p, td')).filter(visible)
    .map((el) => parseFloat(getComputedStyle(el).fontSize) || 0)
    .filter((s) => s > 0);
  if (titles.length > 0 && bodyFonts.length > 0) {
    const titleSize = Math.max(...titles.map((t) => parseFloat(getComputedStyle(t).fontSize) || 0));
    const bodySize = Math.max(...bodyFonts);
    if (titleSize > 0 && titleSize <= bodySize) {
      issues.push({type: 'visual_hierarchy', element: '.sapMTitle',
        severity: 'medium',
        suggestion: 'page title (' + titleSize + 'px) is not larger than body text (' + bodySize + 'px)'});
    }
  }

  page.querySelectorAll('.sapMBtn, button').forEach((b) => {
    if (!visible(b)) return;
    const st = getComputedStyle(b);
    if (st.cursor === 'default' && !b.hasAttribute('disabled')) {
      issues.push({type: 'interaction_affordance', element: describe(b),
        severity: 'low', suggestion: 'set cursor: pointer on clickable controls'});
    }
  });

  const inputs = Array.from(page.querySelectorAll('input, select, textarea')).filter(visible);
  inputs.forEach((el) => {
    const st = getComputedStyle(el);
    const size = parseFloat(st.fontSize) || 0;
    if (size > 0 && size < 16) {
      issues.push({type: 'touch_target', element: describe(el),
        severity: 'low', suggestion: 'inputs with font-size under 16px zoom on iOS focus — bump to 16px'});
    }
  });

  return issues;
}
"""


def critique_ux(page: Page) -> list[UxIssue]:
    """Run the visual critique against the visible UI5 page."""
    try:
        raw = page.evaluate(_CRITIQUE_SCRIPT)
    except Exception:
        return []
    issues = []
    for entry in raw or []:
        issues.append(
            UxIssue(
                type=str(entry.get("type", "unknown")),
                element=str(entry.get("element", "")),
                severity=Severity(str(entry.get("severity", Severity.LOW.value))),
                suggestion=str(entry.get("suggestion", "")),
            )
        )
    return issues
