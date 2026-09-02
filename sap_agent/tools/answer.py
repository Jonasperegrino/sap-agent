"""Deterministic answer tool (issues #651, #647): table-backed Q&A.

Rules:
- answers are cross-validated against the rendered table in the same run
  (truthfulness) — never recalled from memory
- unknown columns → unsupported; zero matches → not_found (explicit, not 0)
- deterministic checksum lets repeated runs prove stability
- intents parsed by `reason.parse_question`; ambiguity surfaces as follow_up
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from playwright.sync_api import Page

from ..context import SessionContext
from ..schemas import AnsweredQuestion, AnswerEvidence, IntentConfig, QuestionIntent
from ..ui5.bridge import current_route
from .extract import TableData, get_table_data
from .nav import navigate
from .reason import parse_question, parse_question_with_llm

OBJECT_STATUS_NOISE = "Object Status"


@dataclass(frozen=True)
class _TableSnapshot:
    data: TableData
    columns: dict[str, int]


def _snapshot(page: Page, ctx: SessionContext) -> _TableSnapshot:
    data = get_table_data(page)
    columns = {name: idx for idx, name in enumerate(data.columns)}
    ctx.record(
        "extract",
        "table.snapshot",
        outcome=f"columns={len(data.columns)} rows={data.row_count}",
        url=page.url,
    )
    return _TableSnapshot(data=data, columns=columns)


def _normalize(cell: str) -> str:
    """Strip UI5 ObjectStatus wrapper text so the label is comparable."""
    if OBJECT_STATUS_NOISE in cell:
        cell = cell.split(OBJECT_STATUS_NOISE, 1)[0]
    return cell.strip()


def _checksum(payload: AnsweredQuestion) -> str:
    # answer may be int or list[dict] (aggregate); serialize deterministically
    ans = payload.answer
    if isinstance(ans, list):
        ans = json.dumps(ans, sort_keys=True)
    stable = {
        "question": payload.question,
        "answer": ans,
        "not_found": payload.not_found,
        "unsupported": payload.unsupported,
        "matched_rows": payload.evidence.matched_rows,
        "column": payload.evidence.column,
        "intent": payload.intent.value,
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _parse_amount(value: str | float | int | None) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return 0.0
    cleaned = value.replace("€", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except Exception:
        return 0.0


def _resolve_json_key(column: str | None) -> str:
    mapping = {
        "amount": "amountEur",
        "customer": "customer",
        "built": "built",
        "status": "status",
        "price": "price",
        "stock": "stock",
        "category": "category",
        "name": "name",
    }
    if not column:
        return ""
    return mapping.get(column.lower(), column.lower())


def _matches(cell: str, value: str, comparer: str) -> bool:
    if comparer == "year":
        return cell[:4] == value if len(cell) >= 4 else False
    normalized = _normalize(cell)
    return normalized.lower() == value.lower()


def _freeze(result: AnsweredQuestion, ctx: SessionContext) -> AnsweredQuestion:
    result.checksum = _checksum(result)
    ctx.record("answer", result.intent.value, result.message or "answered")
    return result


def _aggregate_top(
    question: str,
    intent: IntentConfig,
    ctx: SessionContext,
    *,
    source: str,
    endpoint: str | None,
    snapshot: _TableSnapshot | None = None,
    network_rows: list[dict] | None = None,
) -> AnsweredQuestion | None:
    """Handle AGGREGATE: sum/avg grouped + ranked. Prefers network_rows (precise numeric)."""
    agg_col = _resolve_json_key(intent.aggregation_column or intent.column or "amount")
    group_key = _resolve_json_key(intent.group_by or "customer")
    if not agg_col or not group_key:
        return AnsweredQuestion(
            question=question,
            intent=QuestionIntent.AGGREGATE,
            unsupported=True,
            message="aggregate needs aggregation_column and group_by",
            follow_up="e.g. sum amount grouped by customer",
        )
    limit = intent.limit or 3
    # source rows
    rows: list[dict] = []
    if network_rows is not None:
        rows = network_rows
    elif snapshot is not None:
        # fallback: build dicts from table snapshot using column names
        col_map = {name.lower(): idx for idx, name in enumerate(snapshot.data.columns)}
        agg_idx = col_map.get((intent.aggregation_column or "amount").lower())
        grp_idx = col_map.get((intent.group_by or "customer").lower())
        filter_idx = col_map.get(intent.column.lower()) if intent.column else None
        for r in snapshot.data.rows:
            if filter_idx is not None and intent.value:
                cell = r[filter_idx] if filter_idx < len(r) else ""
                if not _matches(cell, intent.value, intent.comparer):
                    continue
            grp = r[grp_idx] if grp_idx is not None and grp_idx < len(r) else ""
            amt_raw = r[agg_idx] if agg_idx is not None and agg_idx < len(r) else "0"
            grp = _normalize(grp)
            rows.append(
                {
                    group_key: grp,
                    agg_col: _parse_amount(amt_raw),
                    "built": r[filter_idx] if filter_idx is not None else "",
                }
            )
        # rows already filtered, no second pass
        filtered = rows
        # group
        totals: dict[str, float] = {}
        for rec in filtered:
            g = str(rec.get(group_key, "")).strip()
            if not g:
                continue
            totals[g] = totals.get(g, 0.0) + float(rec.get(agg_col, 0) or 0)
        ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=intent.sort_order != "asc")[:limit]
        answer = [{"customer": k, "revenue": round(v, 2), "amount": round(v, 2)} for k, v in ranked]
        if not answer:
            return AnsweredQuestion(
                question=question,
                intent=QuestionIntent.AGGREGATE,
                not_found=True,
                message="no rows for aggregate filter",
                evidence=AnswerEvidence(source=source, column=intent.column or "", matched_rows=0, endpoint=endpoint),
                confidence="high",
            )
        return AnsweredQuestion(
            question=question,
            intent=QuestionIntent.AGGREGATE,
            answer=answer,
            evidence=AnswerEvidence(
                source=source, column=intent.column or "", matched_rows=len(filtered), endpoint=endpoint
            ),
            confidence="medium",
        )
    else:
        return None

    # network path: filter + group
    filtered = []
    for rec in rows:
        if intent.column and intent.value:
            cell = str(rec.get(_resolve_json_key(intent.column), ""))
            if not _matches(cell, intent.value, intent.comparer):
                continue
        filtered.append(rec)
    totals: dict[str, float] = {}
    for rec in filtered:
        g = str(rec.get(group_key, "")).strip()
        if not g:
            continue
        val = _parse_amount(rec.get(agg_col, rec.get("amount", 0)))
        totals[g] = totals.get(g, 0.0) + val
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=intent.sort_order != "asc")[:limit]
    answer = [{"customer": k, "revenue": round(v, 2)} for k, v in ranked]
    if not answer:
        return AnsweredQuestion(
            question=question,
            intent=QuestionIntent.AGGREGATE,
            not_found=True,
            message="no rows for aggregate filter",
            evidence=AnswerEvidence(source=source, column=intent.column or "", matched_rows=0, endpoint=endpoint),
            confidence="high",
        )
    return AnsweredQuestion(
        question=question,
        intent=QuestionIntent.AGGREGATE,
        answer=answer,
        evidence=AnswerEvidence(
            source=source, column=intent.column or "", matched_rows=len(filtered), endpoint=endpoint
        ),
        confidence="high",
    )


def evaluate_question(
    page: Page,
    question: str,
    ctx: SessionContext,
    *,
    source: str = "salesTable",
    endpoint: str | None = None,
    intent: IntentConfig | None = None,
    route: str | None = None,
    app_url: str = "",
    capture=None,
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
        except Exception:
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

    if route is not None and current_route(page) != "#/" + route:
        navigate(page, route, app_url)
        ctx.record("nav", f"navigate.{route}", outcome="landed", url=page.url)

    snapshot = _snapshot(page, ctx)

    # AGGREGATE branch: needs network JSON when available (precise sum), else table fallback
    if intent.intent == QuestionIntent.AGGREGATE:
        network_rows = None
        if capture is not None:
            try:
                body = capture.latest_response_body("sales.json")
                if body is None:
                    body = capture.latest_response_body("sales")
                if isinstance(body, list) and body and isinstance(body[0], dict):
                    network_rows = body
            except Exception:
                network_rows = None
        agg = _aggregate_top(
            question, intent, ctx, source=source, endpoint=endpoint, snapshot=snapshot, network_rows=network_rows
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
    matched = [row for row in snapshot.data.rows if _matches(row[idx], intent.value, intent.comparer)]

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
    page: Page,
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
        source="salesTable",
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
    result.checksum = _checksum(result)
    return result
