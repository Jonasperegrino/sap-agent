"""Decision layer (WP2 thin gate): fixed chain jev,llm,rules.

Thresholds: 0.80 intent, 0.75 severity/verdict. Answer-level confidence only.
No-confidence accepted only if validates + agrees rules on enums. Else
follow-up (intent) or needs-human-review (severity/verdict). Allow-list
payloads only. JSONL log per run.
# ponytail: fixed chain, single threshold. Env config + per-field split if second model matters.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from ..context import SessionContext

INTENT_THRESHOLD = 0.80
SEVERITY_THRESHOLD = 0.75
VERDICT_THRESHOLD = 0.75


def allow_listed(kind: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Keep only allow-listed fields per kind; never credentials/DOM/secrets."""
    allow = {
        "intent": {"question"},
        "severity": {"issue_type", "route", "flow", "element_role", "measured", "page_state"},
        "verdict": {"delta", "explained_by", "page_labels", "scope_text", "kpi_label"},
    }.get(kind, set())
    return {k: v for k, v in fields.items() if k in allow}


def _log(ctx: SessionContext | None, path: Path | None, entry: dict[str, Any]) -> None:
    line = json.dumps(entry, sort_keys=True, default=str)
    if ctx is not None:
        ctx.record("decide", str(entry.get("kind", "?")), str(entry.get("outcome", "")))
    if path is not None:
        with path.open("a") as fh:
            fh.write(line + "\n")


def decide_verdict(
    delta: float,
    explained: bool,
    pending_older_than_scope: bool,
    closed_only: bool,
    ctx: SessionContext | None = None,
    log_path: Path | None = None,
) -> tuple[str, str, float | str]:
    """Rules baseline verdict (WP3); JEV shown via recorded example, not live in demo."""
    if explained and abs(delta) > 0:
        verdict, sev = "bug", "medium"
    elif pending_older_than_scope:
        verdict, sev = "needs_owner_input", "low"
    elif closed_only:
        verdict, sev = "by_design", "low"
    else:
        verdict, sev = "needs_owner_input", "low"
    _log(
        ctx,
        log_path,
        {
            "kind": "verdict",
            "provider": "rules",
            "value": verdict,
            "confidence": "none",
            "severity": sev,
            "outcome": "accepted",
        },
    )
    return verdict, sev, "none"
