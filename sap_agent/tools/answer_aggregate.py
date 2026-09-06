"""Aggregation answers (split from answer.py): sum/avg/count grouped + ranked.

Prefers captured network JSON (precise numerics); falls back to the rendered
table snapshot. Customer-group joins resolve via customers.json.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..schemas import AnsweredQuestion, AnswerEvidence, IntentConfig, QuestionIntent
from .answer_core import _matches, _normalize, _parse_amount, _resolve_json_key, _TableSnapshot, fetch_json_body

if TYPE_CHECKING:
    from ..protocols import CaptureLike


def _customer_join(capture: CaptureLike | None, filename: str, group_key: str) -> tuple[dict[str, str], dict[str, str]]:
    """Map customer id/name → group value from a captured JSON file."""
    by_id: dict[str, str] = {}
    by_name: dict[str, str] = {}
    body = fetch_json_body(capture, filename)
    if body:
        for c in body:
            by_id[str(c.get("id", ""))] = str(c.get(group_key, "")).strip()
            by_name[str(c.get("name", "")).lower()] = str(c.get(group_key, "")).strip()
    return by_id, by_name


#: group keys resolved via customers.json instead of the row itself
_GEO_GROUPS = ("industry", "city", "country")


def _accumulate(
    records: list[dict],
    is_count: bool,
    is_avg: bool,
    num_avg,
    num_sum,
    group_of,
) -> tuple[dict[str, float], dict[str, float], dict[str, int]]:
    """Shared group accumulation for snapshot + network paths."""
    totals: dict[str, float] = {}
    avg_sums: dict[str, float] = {}
    avg_counts: dict[str, int] = {}
    for rec in records:
        g = group_of(rec)
        if not g:
            continue
        if is_count:
            totals[g] = totals.get(g, 0.0) + 1
        elif is_avg:
            avg_sums[g] = avg_sums.get(g, 0.0) + num_avg(rec)
            avg_counts[g] = avg_counts.get(g, 0) + 1
        else:
            totals[g] = totals.get(g, 0.0) + num_sum(rec)
    return totals, avg_sums, avg_counts


def _ungrouped_avg(records: list[dict], num_avg) -> list[dict]:
    vals = [num_avg(r) for r in records]
    return [{"average": round(sum(vals) / len(vals), 2) if vals else 0.0}]


def _aggregate_response(
    question: str,
    answer: list[dict],
    *,
    source: str,
    column: str,
    matched: int,
    endpoint: str | None,
    confidence: str,
) -> AnsweredQuestion:
    if not answer:
        return AnsweredQuestion(
            question=question,
            intent=QuestionIntent.AGGREGATE,
            not_found=True,
            message="no rows for aggregate filter",
            evidence=AnswerEvidence(source=source, column=column, matched_rows=0, endpoint=endpoint),
            confidence="high",
        )
    return AnsweredQuestion(
        question=question,
        intent=QuestionIntent.AGGREGATE,
        answer=answer,
        evidence=AnswerEvidence(source=source, column=column, matched_rows=matched, endpoint=endpoint),
        confidence=confidence,
    )


def _ranked_answer(
    totals: dict[str, float],
    avg_sums: dict[str, float],
    avg_counts: dict[str, int],
    group_key: str,
    intent: IntentConfig,
    limit: int,
    is_count: bool,
    is_avg: bool,
    *,
    include_amount: bool = False,
) -> list[dict]:
    """Shared group/rank tail for snapshot + network paths."""
    key_name = "customer" if group_key == "customer" else group_key
    if is_avg:
        avg_by_key = {k: round(avg_sums[k] / avg_counts[k], 2) for k in avg_sums}
        ranked = sorted(avg_by_key.items(), key=lambda kv: kv[1], reverse=intent.sort_order != "asc")[:limit]
        return [{key_name: k, "average": v} for k, v in ranked]
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=intent.sort_order != "asc")[:limit]
    if is_count:
        return [{key_name: k, "count": int(v)} for k, v in ranked]
    if include_amount:
        return [{key_name: k, "revenue": round(v, 2), "amount": round(v, 2)} for k, v in ranked]
    return [{key_name: k, "revenue": round(v, 2)} for k, v in ranked]


def _aggregate_top(
    question: str,
    intent: IntentConfig,
    *,
    source: str,
    endpoint: str | None,
    snapshot: _TableSnapshot | None = None,
    network_rows: list[dict] | None = None,
    capture: CaptureLike | None = None,
) -> AnsweredQuestion | None:
    """Handle AGGREGATE: sum/avg/count grouped + ranked. Prefers network_rows (precise numeric)."""
    agg_col = _resolve_json_key(intent.aggregation_column or intent.column or "amount")
    group_key = _resolve_json_key(intent.group_by or "customer")
    is_count = intent.aggregation == "count"
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
        # join handling for industry/country/city — map customer name → group value via customers.json
        join_by_name: dict[str, str] = {}
        if group_key in _GEO_GROUPS:
            _, join_by_name = _customer_join(capture, "customers.json", group_key)
        # group
        is_avg = intent.aggregation == "avg"

        def _num(rec: dict) -> float:
            return float(rec.get(agg_col, 0) or 0)

        def _snap_group(rec: dict) -> str:
            if group_key in _GEO_GROUPS and join_by_name:
                cust = str(rec.get("customer", rec.get("Customer", ""))).lower()
                if not cust:
                    cust = str(rec.get(group_key, "")).lower()
                return join_by_name.get(cust, "").strip()
            return str(rec.get(group_key, "")).strip()

        if is_avg and intent.group_by is None:
            answer = _ungrouped_avg(filtered, _num)
        else:
            totals, avg_sums, avg_counts = _accumulate(filtered, is_count, is_avg, _num, _num, _snap_group)
            answer = _ranked_answer(
                totals, avg_sums, avg_counts, group_key, intent, limit, is_count, is_avg, include_amount=True
            )
        return _aggregate_response(
            question,
            answer,
            source=source,
            column=intent.column or "",
            matched=len(filtered),
            endpoint=endpoint,
            confidence="medium",
        )
    else:
        return None

    # network path: filter + group (join for country/industry/city filter)
    # build filter join map if needed (sales.customerId → customers.country)
    filter_join_by_id: dict[str, str] = {}
    filter_join_by_name: dict[str, str] = {}
    if intent.column and intent.column.lower() in _GEO_GROUPS:
        filter_join_by_id, filter_join_by_name = _customer_join(capture, "customers.json", intent.column.lower())
    filtered = []
    for rec in rows:
        if intent.column and intent.value:
            col_low = intent.column.lower()
            if col_low in _GEO_GROUPS and (filter_join_by_id or filter_join_by_name):
                cid = str(rec.get("customerId", "")).strip()
                cname = str(rec.get("customer", "")).lower().strip()
                cell = filter_join_by_id.get(cid, "") or filter_join_by_name.get(cname, "")
                if not _matches(cell, intent.value, intent.comparer) and intent.value.lower() not in cell.lower():
                    continue
                # matched
            else:
                cell = str(rec.get(_resolve_json_key(intent.column), ""))
                if not _matches(cell, intent.value, intent.comparer):
                    continue
        filtered.append(rec)
    # join map for industry/city/country via customers.json (sales.customerId → customers.industry)
    join_by_id: dict[str, str] = {}
    join_by_name: dict[str, str] = {}
    if group_key in _GEO_GROUPS:
        join_by_id, join_by_name = _customer_join(capture, "customers.json", group_key)
    is_avg = intent.aggregation == "avg"

    def _net_avg(rec: dict) -> float:
        return float(rec.get(agg_col, rec.get("amount", 0)) or 0)

    def _net_sum(rec: dict) -> float:
        return _parse_amount(rec.get(agg_col, rec.get("amount", 0)))

    def _net_group(rec: dict) -> str:
        if group_key in _GEO_GROUPS and (join_by_id or join_by_name):
            cid = str(rec.get("customerId", "")).strip()
            cname = str(rec.get("customer", "")).lower().strip()
            return (join_by_id.get(cid, "") or join_by_name.get(cname, "")).strip()
        return str(rec.get(group_key, "")).strip()

    if is_avg and intent.group_by is None:
        answer = _ungrouped_avg(filtered, _net_avg)
    else:
        totals, avg_sums, avg_counts = _accumulate(filtered, is_count, is_avg, _net_avg, _net_sum, _net_group)
        answer = _ranked_answer(totals, avg_sums, avg_counts, group_key, intent, limit, is_count, is_avg)
    return _aggregate_response(
        question,
        answer,
        source=source,
        column=intent.column or "",
        matched=len(filtered),
        endpoint=endpoint,
        confidence="high",
    )
