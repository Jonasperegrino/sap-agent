"""Deterministic answer tool (issues #651, #647): table-backed Q&A.

Rules:
- answers are cross-validated against the rendered table in the same run
  (truthfulness) — never recalled from memory
- unknown columns → unsupported; zero matches → not_found (explicit, not 0)
- deterministic checksum lets repeated runs prove stability
- intents parsed by `reason.parse_question`; ambiguity surfaces as follow_up

Layout: shared helpers live in answer_core, aggregation in
answer_aggregate, entity lookups in answer_lookup. This module keeps the
orchestrator (evaluate_question + answer_count_by_status) and re-exports
the helpers tests import.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from playwright.sync_api import Error as PlaywrightError

from ..schemas import AnsweredQuestion, AnswerEvidence, IntentConfig, QuestionIntent
from ..ui5.bridge import current_route
from .answer_aggregate import _aggregate_top
from .answer_core import (
    _checksum as _checksum,
)
from .answer_core import (
    _freeze,
    _snapshot,
    _wait_for_table_rows,
    fetch_json_body,
)
from .answer_core import (
    _infer_auto_route as _infer_auto_route,
)
from .answer_core import (
    _matches as _matches,
)
from .answer_core import (
    _normalize as _normalize,
)
from .answer_core import (
    _parse_amount as _parse_amount,
)
from .answer_core import (
    _resolve_json_key as _resolve_json_key,
)
from .answer_lookup import _lookup_customer as _lookup_customer
from .answer_lookup import _lookup_product
from .nav import navigate
from .reason import parse_question, parse_question_with_llm

if TYPE_CHECKING:
    from ..context import SessionContext
    from ..protocols import CaptureLike, PageLike

logger = logging.getLogger(__name__)


def evaluate_question(
    page: PageLike,
    question: str,
    ctx: SessionContext,
    *,
    source: str = "salesTable",
    endpoint: str | None = None,
    intent: IntentConfig | None = None,
    route: str | None = None,
    app_url: str = "",
    capture: CaptureLike | None = None,
) -> AnsweredQuestion:
    """Answer a natural-language question from the rendered table (intent-aware).

    `intent` may be supplied directly (deterministic callers) instead of being
    parsed from the question text. `route` navigates to a top-level page first
    (e.g. 'orders', 'catalog') so the question is answered against that page's
    table; defaults to the current page — backward compatible.
    `capture` optional NetworkCapture for precise numeric aggregation (prefers JSON amountEur).
    """
    if intent is None:
        # LLM slot: rule first, LLM fallback if configured
        try:
            intent = parse_question_with_llm(question, getattr(ctx, "config", None), ctx)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, TimeoutError) as exc:
            logger.debug("llm slot skipped: %s", exc)
            intent = parse_question(question)
    if intent.intent == QuestionIntent.UNSUPPORTED:
        return _freeze(
            AnsweredQuestion(
                question=question,
                intent=QuestionIntent.UNSUPPORTED,
                unsupported=True,
                message=intent.follow_up or "unsupported question type",
                follow_up=intent.follow_up,
            ),
            ctx,
        )

    # LOOKUP — customer/product queries, handle before generic route nav
    if intent.intent == QuestionIntent.LOOKUP:
        # try product first when column is product-specific
        if (intent.column or "").lower() in ("price", "stock", "category", "unit"):
            result = _lookup_product(question, intent, ctx, page, app_url, capture)
            if result is not None:
                return result
        result = _lookup_customer(question, intent, ctx, page, app_url, capture)
        if result is not None:
            return result
        # fallback try product if customer missed (name ambiguous)
        result = _lookup_product(question, intent, ctx, page, app_url, capture)
        if result is not None:
            return result
        # if lookup helper couldn't resolve (e.g. unknown column), fall through to generic unsupported
        return _freeze(
            AnsweredQuestion(
                question=question,
                intent=QuestionIntent.LOOKUP,
                unsupported=True,
                message=intent.follow_up or "unsupported lookup",
                follow_up="try: who is contact at Acme Corp?",
            ),
            ctx,
        )

    # auto-route when no explicit route (Streamlit) — infer page from intent
    if route is not None and current_route(page) != "#/" + route:
        navigate(page, route, app_url)
        ctx.record("nav", f"navigate.{route}", outcome="landed", url=page.url)
    elif route is None:
        auto = _infer_auto_route(intent)
        if auto is not None and current_route(page) != "#/" + auto:
            try:
                navigate(page, auto, app_url)
                ctx.record("nav", f"navigate.{auto}", outcome="auto", url=page.url)
                # customers/catalog tables load async via fetch — probe for rows
                _wait_for_table_rows(page)
            except PlaywrightError:
                pass

    snapshot = _snapshot(page, ctx)
    # retry snapshot if customers table still busy (rows=1 header only)
    if snapshot.data.row_count == 1 and any(c.lower() == "location" for c in snapshot.columns):
        _wait_for_table_rows(page)
        with contextlib.suppress(PlaywrightError, AttributeError, ValueError, TypeError):
            snapshot = _snapshot(page, ctx)

    # AGGREGATE branch: needs network JSON when available (precise sum), else table fallback
    if intent.intent == QuestionIntent.AGGREGATE:
        network_rows = None
        if capture is not None:
            # pick JSON source by columns needed: product columns -> products.json
            agg_col = (intent.aggregation_column or "").lower()
            group_by = (intent.group_by or "").lower()
            product_cols = {"price", "stock", "category", "name", "unit"}
            json_key = "products.json" if agg_col in product_cols or group_by in product_cols else "sales.json"
            network_rows = fetch_json_body(capture, json_key) or fetch_json_body(
                capture, json_key[: json_key.rfind(".")]
            )
        agg = _aggregate_top(
            question,
            intent,
            source=source,
            endpoint=endpoint,
            snapshot=snapshot,
            network_rows=network_rows,
            capture=capture,
        )
        if agg is not None:
            if agg.unsupported or agg.not_found:
                return _freeze(agg, ctx)
            ctx.record(
                "answer", "aggregate", outcome=f"groups={len(agg.answer) if isinstance(agg.answer, list) else 0}"
            )
            return _freeze(agg, ctx)
        return _freeze(
            AnsweredQuestion(
                question=question,
                intent=QuestionIntent.AGGREGATE,
                unsupported=True,
                message="aggregate requires captured sales data",
                follow_up="ensure sales.json captured",
            ),
            ctx,
        )

    lookup = {name.lower(): name for name in snapshot.columns}
    resolved = lookup.get(intent.column.lower()) if intent.column else None
    # country/city are inside Location "Berlin, Germany"
    if resolved is None and intent.column and intent.column.lower() in ("country", "city") and "location" in lookup:
        resolved = lookup["location"]
        # handle COUNT_WHERE via substring in Location
        if intent.intent in (QuestionIntent.COUNT_WHERE, QuestionIntent.COUNT_TOTAL, QuestionIntent.EXISTENCE):
            idx = snapshot.columns[resolved]
            matched = (
                [row for row in snapshot.data.rows if idx < len(row) and intent.value.lower() in row[idx].lower()]
                if intent.value
                else []
            )
            if not matched and intent.intent != QuestionIntent.COUNT_TOTAL:
                result = AnsweredQuestion(
                    question=question,
                    intent=intent.intent,
                    not_found=True,
                    message=f"no rows with {intent.column} = {intent.value!r}",
                    evidence=AnswerEvidence(source=source, column=intent.column, matched_rows=0, endpoint=endpoint),
                    confidence="high",
                )
                return _freeze(result, ctx)
            if intent.intent == QuestionIntent.COUNT_WHERE:
                result = AnsweredQuestion(
                    question=question,
                    intent=QuestionIntent.COUNT_WHERE,
                    answer=len(matched),
                    evidence=AnswerEvidence(
                        source=source, column=intent.column, matched_rows=len(matched), endpoint=endpoint
                    ),
                    confidence="high",
                )
                ctx.record(
                    "answer",
                    "count_where",
                    outcome=f"count={result.answer}",
                    detail=f"matched {len(matched)} via Location",
                )
                return _freeze(result, ctx)
            if intent.intent == QuestionIntent.EXISTENCE:
                result = AnsweredQuestion(
                    question=question,
                    intent=QuestionIntent.EXISTENCE,
                    answer=1 if matched else 0,
                    evidence=AnswerEvidence(
                        source=source, column=intent.column, matched_rows=len(matched), endpoint=endpoint
                    ),
                    confidence="high",
                )
                return _freeze(result, ctx)
        else:
            # for other intents, treat as Location column
            intent.column = resolved
            resolved = lookup["location"]
    if intent.column and resolved is None:
        result = AnsweredQuestion(
            question=question,
            intent=intent.intent,
            unsupported=True,
            message=f"column {intent.column!r} not present in table",
            follow_up=f"available columns: {', '.join(snapshot.columns)}",
        )
        return _freeze(result, ctx)
    if intent.column:
        intent.column = resolved

    if intent.intent == QuestionIntent.COUNT_TOTAL:
        if not snapshot.columns or not snapshot.data.rows:
            result = AnsweredQuestion(
                question=question,
                intent=QuestionIntent.COUNT_TOTAL,
                unsupported=True,
                message="table did not load — no columns or rows extracted",
                follow_up="retry once the table renders",
            )
            ctx.record("answer", "count_total", outcome="empty-snapshot", detail="unsupported, not 0")
            return _freeze(result, ctx)
        result = AnsweredQuestion(
            question=question,
            intent=QuestionIntent.COUNT_TOTAL,
            answer=snapshot.data.row_count,
            evidence=AnswerEvidence(source=source, column="", matched_rows=snapshot.data.row_count, endpoint=endpoint),
            confidence="high",
        )
        ctx.record(
            "answer",
            "count_total",
            outcome=f"count={result.answer}",
            detail=f"total rendered rows={snapshot.data.row_count}",
        )
        return _freeze(result, ctx)

    if intent.value is None and intent.column:
        return _freeze(
            AnsweredQuestion(
                question=question,
                intent=intent.intent,
                unsupported=True,
                message=f"need a value to filter {intent.column!r} by",
                follow_up=intent.follow_up or "which value?",
            ),
            ctx,
        )

    if not intent.column or not intent.value:
        return _freeze(
            AnsweredQuestion(
                question=question,
                intent=intent.intent,
                unsupported=True,
                message="column and value are required for count_where",
                follow_up=intent.follow_up or "which column and value?",
            ),
            ctx,
        )
    idx = snapshot.columns[intent.column]
    matched = [
        row for row in snapshot.data.rows if idx < len(row) and _matches(row[idx], intent.value, intent.comparer)
    ]

    if not matched:
        result = AnsweredQuestion(
            question=question,
            intent=intent.intent,
            not_found=True,
            message=f"no rows with {intent.column} = {intent.value!r}",
            evidence=AnswerEvidence(source=source, column=intent.column, matched_rows=0, endpoint=endpoint),
            confidence="high",
        )
        return _freeze(result, ctx)

    if intent.intent == QuestionIntent.EXISTENCE:
        result = AnsweredQuestion(
            question=question,
            intent=QuestionIntent.EXISTENCE,
            answer=1,
            evidence=AnswerEvidence(
                source=source,
                column=intent.column,
                matched_rows=len(matched),
                endpoint=endpoint,
            ),
            confidence="high",
        )
        ctx.record(
            "answer",
            "existence",
            outcome="exists",
            detail=f"{len(matched)} rows match {intent.column}={intent.value!r} ({intent.comparer})",
        )
        return _freeze(result, ctx)

    result = AnsweredQuestion(
        question=question,
        intent=QuestionIntent.COUNT_WHERE,
        answer=len(matched),
        evidence=AnswerEvidence(
            source=source,
            column=intent.column,
            matched_rows=len(matched),
            endpoint=endpoint,
        ),
        confidence="high",
    )
    ctx.record(
        "answer",
        "count_where",
        outcome=f"count={result.answer}",
        detail=f"matched {len(matched)} rows on {intent.column}={intent.value!r} ({intent.comparer})",
    )
    return _freeze(result, ctx)


def answer_count_by_status(
    page: PageLike,
    status: str,
    ctx: SessionContext,
    *,
    column: str = "Status",
    source: str = "salesTable",
    endpoint: str | None = None,
) -> AnsweredQuestion:
    """Count rows whose `column` equals `status`, with evidence (legacy #651 entry)."""
    question = f"how many rows have {column} = {status!r}"
    result = evaluate_question(
        page,
        question,
        ctx,
        source=source,
        endpoint=endpoint,
        intent=IntentConfig(
            intent=QuestionIntent.COUNT_WHERE,
            column=column,
            value=status,
            comparer="exact",
        ),
    )
    # legacy callers expect evidence.column == "Status" even when intent inferred
    # differently; normalize the evidence column to the explicit argument
    result.evidence.column = column
    return _freeze(result, ctx)
