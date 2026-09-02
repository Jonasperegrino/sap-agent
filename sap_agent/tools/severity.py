"""Severity classification for QA findings (#698).

Issue types produced by the in-browser audit scripts are mapped to a severity
following the #698 rules: HIGH = accessibility violations (WCAG A), broken
functionality, security; MEDIUM = UX inconsistencies, missing form labels,
poor contrast; LOW = minor styling, suggestions, nitpicks.
"""

from __future__ import annotations

from ..schemas import Severity

_HIGH_TYPES = frozenset({"missing_alt", "missing_label"})

_MEDIUM_TYPES = frozenset(
    {
        "heading_order",
        "form_label",
        "contrast",
        "visual_hierarchy",
        "spacing_inconsistency",
        "alignment_issue",
        "interaction_affordance",
        "page_consistency",
    }
)

_LOW_TYPES = frozenset(
    {
        "empty_alt",
        "heading_hierarchy",
        "touch_target",
    }
)


def classify_issue(issue_type: str) -> Severity:
    """Map an issue type to its demo severity (unknown types default to LOW)."""
    if issue_type in _HIGH_TYPES:
        return Severity.HIGH
    if issue_type in _MEDIUM_TYPES:
        return Severity.MEDIUM
    return Severity.LOW
