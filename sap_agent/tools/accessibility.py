"""Accessibility audit tool (issue #687): deterministic WCAG-oriented checks.

Runs inside the browser via page.evaluate against the visible UI5 page
(scoped like `extract` so stale hidden views cannot double-report). Checks:
missing alt text, missing accessible names on interactive controls, heading
order, form-label association, and a basic text/background contrast ratio.
"""

from __future__ import annotations

from playwright.sync_api import Page

from ..schemas import AccessibilityIssue, Severity

_VISIBLE_PAGE = ".sapMPage:visible"

_AUDIT_SCRIPT = """
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

  const parseRgb = (c) => {
    const m = c.match(/rgba?\\(([\\d.]+),\\s*([\\d.]+),\\s*([\\d.]+)/);
    return m ? [parseFloat(m[1]), parseFloat(m[2]), parseFloat(m[3])] : null;
  };
  const lum = (rgb) => {
    const f = (v) => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]);
  };
  const contrast = (a, b) => {
    const [l1, l2] = [lum(parseRgb(a)), lum(parseRgb(b))].sort((x, y) => y - x);
    return (l1 + 0.05) / (l2 + 0.05);
  };

  page.querySelectorAll('img').forEach((img) => {
    if (!visible(img)) return;
    const alt = img.getAttribute('alt');
    if (alt === null) {
      issues.push({type: 'missing_alt', element: describe(img),
        severity: 'high', suggestion: 'add an alt attribute describing the image content'});
    } else if (alt.trim() === '' && !img.closest('button, a')) {
      issues.push({type: 'empty_alt', element: describe(img),
        severity: 'low', suggestion: 'decorative images should use alt="" intentionally'});
    }
  });

  const controls = 'a[href], button, input, select, textarea, [role="button"]';
  page.querySelectorAll(controls).forEach((el) => {
    if (!visible(el)) return;
    const hasName = el.getAttribute('aria-label') ||
      el.getAttribute('aria-labelledby') ||
      el.getAttribute('title') ||
      (el.textContent || '').trim() ||
      (el.getAttribute('placeholder') || '') ||
      (el.id && document.querySelector('label[for="' + el.id + '"]')) ||
      el.closest('label');
    if (!hasName) {
      issues.push({type: 'missing_label', element: describe(el),
        severity: 'high', suggestion: 'add aria-label or associated label text for the control'});
    }
  });

  const headings = Array.from(page.querySelectorAll('h1,h2,h3,h4,h5,h6')).filter(visible);
  const levels = headings.map((h) => parseInt(h.tagName[1], 10));
  headings.forEach((h, i) => {
    const lvl = levels[i];
    if (i === 0 && lvl > 1) {
      issues.push({type: 'heading_order', element: h.outerHTML.slice(0, 80),
        severity: 'medium', suggestion: 'start the page with an h1 heading'});
    } else if (i > 0 && lvl > levels[i - 1] + 1) {
      issues.push({type: 'heading_order', element: h.outerHTML.slice(0, 80),
        severity: 'medium',
        suggestion: `do not skip heading levels (h${levels[i - 1]} should not jump to h${lvl})`});
    }
  });
  if (headings.length > 0 && levels[0] > 1) {
    issues.push({type: 'heading_hierarchy', element: 'h' + levels[0],
      severity: 'low', suggestion: 'ensure exactly one h1 per page'});
  }

  const bodyBg = getComputedStyle(document.body).backgroundColor;
  page.querySelectorAll('p, span, label, td, div, h1, h2, h3, h4').forEach((el) => {
    if (!visible(el) || !el.textContent.trim()) return;
    const st = getComputedStyle(el);
    const fg = st.color;
    const bg = st.backgroundColor;
    if (!fg || bg === 'rgba(0, 0, 0, 0)') return;
    const ratio = contrast(fg, bg);
    const size = parseFloat(st.fontSize) || 16;
    const large = size >= 24 || (size >= 18.66 && st.fontWeight >= 700);
    if (ratio < (large ? 3.0 : 4.5)) {
      issues.push({type: 'contrast', element: describe(el),
        severity: 'medium',
        suggestion: `text contrast ${ratio.toFixed(2)}:1 is below the ${large ? 3.0 : 4.5}:1 threshold`});
    }
  });

  page.querySelectorAll('input, select, textarea').forEach((el) => {
    if (!visible(el) || el.getAttribute('aria-label')) return;
    if (el.id && document.querySelector('label[for="' + el.id + '"]')) return;
    if (el.closest('label') || el.getAttribute('placeholder')) return;
    issues.push({type: 'form_label', element: describe(el),
      severity: 'medium', suggestion: 'associate a visible label with the input (label[for])'});
  });

  return issues;
}
"""


def audit_accessibility(page: Page) -> list[AccessibilityIssue]:
    """Run the accessibility audit against the visible UI5 page."""
    try:
        raw = page.evaluate(_AUDIT_SCRIPT)
    except Exception:
        return []
    issues = []
    for entry in raw or []:
        issues.append(
            AccessibilityIssue(
                type=str(entry.get("type", "unknown")),
                element=str(entry.get("element", "")),
                severity=Severity(str(entry.get("severity", Severity.LOW.value))),
                suggestion=str(entry.get("suggestion", "")),
            )
        )
    return issues
