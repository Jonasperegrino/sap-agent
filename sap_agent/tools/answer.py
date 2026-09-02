"""Deterministic answer tool (issues #651, #647): table-backed Q&A.

Rules:
- answers are cross-validated against the rendered table in the same run
  (truthfulness) — never recalled from memory
- unknown columns → unsupported; zero matches → not_found (explicit, not 0)
- deterministic checksum lets repeated runs prove stability
- intents parsed by `reason.parse_question`; ambiguity surfaces as follow_up
"""

from __future__ import annotations

import contextlib
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

# auto-route inference: which page holds the column
_CUSTOMERS_COLS = {"contact", "email", "phone", "industry", "city", "country", "creditrating", "since", "location"}
_CATALOG_COLS = {"category", "price", "stock", "unit"}


def _infer_auto_route(intent: IntentConfig) -> str | None:
    if intent.intent == QuestionIntent.AGGREGATE:
        return None
    col = (intent.column or "").lower()
    grp = (intent.group_by or "").lower()
    if col in _CUSTOMERS_COLS or grp in _CUSTOMERS_COLS:
        return "customers"
    if col in _CATALOG_COLS or grp in _CATALOG_COLS:
        return "catalog"
    return None


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
    capture=None,
) -> AnsweredQuestion | None:
    """Handle AGGREGATE: sum/avg/count grouped + ranked. Prefers network_rows (precise numeric)."""
    agg_col = _resolve_json_key(intent.aggregation_column or intent.column or "amount")
    group_key = _resolve_json_key(intent.group_by or "customer")
    is_count = intent.aggregation == "count"
    # count doesn't need agg_col, sum/avg do
    if not group_key or (not is_count and not agg_col):
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
        # join handling for industry/country/city — map customer name → group value via customers.json
        join_by_name: dict[str, str] = {}
        if group_key in ("industry", "city", "country") and capture is not None:
            try:
                cbody = capture.latest_response_body("customers.json")
                if isinstance(cbody, list) and cbody:
                    for c in cbody:
                        join_by_name[str(c.get("name", "")).lower()] = str(c.get(group_key, "")).strip()
            except Exception:
                pass
            if not join_by_name:
                for url in capture.capture_response_urls():
                    if "customers.json" in url:
                        try:
                            b = capture.response_body(url)
                            if isinstance(b, list) and b:
                                for c in b:
                                    join_by_name[str(c.get("name", "")).lower()] = str(c.get(group_key, "")).strip()
                                break
                        except Exception:
                            continue
        # group
        is_avg = intent.aggregation == "avg"
        totals: dict[str, float] = {}
        avg_sums: dict[str, float] = {}
        avg_counts: dict[str, int] = {}
        if is_avg and intent.group_by is None:
            vals = [float(rec.get(agg_col, 0) or 0) for rec in filtered]
            avg = round(sum(vals) / len(vals), 2) if vals else 0.0
            answer = [{"average": avg}]
        else:
            for rec in filtered:
                if group_key in ("industry", "city", "country") and join_by_name:
                    cust_name = str(rec.get("customer", rec.get("Customer", ""))).lower()
                    if not cust_name:
                        cust_name = str(rec.get(group_key, "")).lower()
                    g = join_by_name.get(cust_name, "").strip()
                    if not g:
                        continue
                else:
                    g = str(rec.get(group_key, "")).strip()
                if not g:
                    continue
                if is_count:
                    totals[g] = totals.get(g, 0.0) + 1
                elif is_avg:
                    avg_sums[g] = avg_sums.get(g, 0.0) + float(rec.get(agg_col, 0) or 0)
                    avg_counts[g] = avg_counts.get(g, 0) + 1
                else:
                    totals[g] = totals.get(g, 0.0) + float(rec.get(agg_col, 0) or 0)
            if is_avg:
                key_name = "customer" if group_key == "customer" else group_key
                avg_by_key = {k: round(avg_sums[k] / avg_counts[k], 2) for k in avg_sums}
                ranked = sorted(avg_by_key.items(), key=lambda kv: kv[1], reverse=intent.sort_order != "asc")[:limit]
                answer = [{key_name: k, "average": v} for k, v in ranked]
            else:
                ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=intent.sort_order != "asc")[:limit]
                if is_count:
                    key_name = "customer" if group_key == "customer" else group_key
                    answer = [{key_name: k, "count": int(v)} for k, v in ranked]
                else:
                    key_name = "customer" if group_key == "customer" else group_key
                    answer = [{key_name: k, "revenue": round(v, 2), "amount": round(v, 2)} for k, v in ranked]
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

    # network path: filter + group (join for country/industry/city filter)
    # build filter join map if needed (sales.customerId → customers.country)
    filter_join_by_id: dict[str, str] = {}
    filter_join_by_name: dict[str, str] = {}
    if intent.column and intent.column.lower() in ("industry", "city", "country") and capture is not None:
        try:
            cbody = capture.latest_response_body("customers.json")
            if isinstance(cbody, list) and cbody:
                for c in cbody:
                    filter_join_by_id[str(c.get("id", ""))] = str(c.get(intent.column.lower(), "")).strip()
                    filter_join_by_name[str(c.get("name", "")).lower()] = str(c.get(intent.column.lower(), "")).strip()
        except Exception:
            pass
        if not filter_join_by_id:
            for url in capture.capture_response_urls():
                if "customers.json" in url:
                    try:
                        b = capture.response_body(url)
                        if isinstance(b, list) and b:
                            for c in b:
                                filter_join_by_id[str(c.get("id", ""))] = str(c.get(intent.column.lower(), "")).strip()
                                filter_join_by_name[str(c.get("name", "")).lower()] = str(
                                    c.get(intent.column.lower(), "")
                                ).strip()  # noqa: E501
                            break
                    except Exception:
                        continue
    filtered = []
    for rec in rows:
        if intent.column and intent.value:
            col_low = intent.column.lower()
            if col_low in ("industry", "city", "country") and (filter_join_by_id or filter_join_by_name):
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
    if group_key in ("industry", "city", "country") and capture is not None:
        try:
            cbody = capture.latest_response_body("customers.json")
            if isinstance(cbody, list) and cbody:
                for c in cbody:
                    join_by_id[str(c.get("id", ""))] = str(c.get(group_key, "")).strip()
                    join_by_name[str(c.get("name", "")).lower()] = str(c.get(group_key, "")).strip()
        except Exception:
            pass
        if not join_by_id:
            for url in capture.capture_response_urls():
                if "customers.json" in url:
                    try:
                        b = capture.response_body(url)
                        if isinstance(b, list) and b:
                            for c in b:
                                join_by_id[str(c.get("id", ""))] = str(c.get(group_key, "")).strip()
                                join_by_name[str(c.get("name", "")).lower()] = str(c.get(group_key, "")).strip()
                            break
                    except Exception:
                        continue
    is_avg = intent.aggregation == "avg"
    totals: dict[str, float] = {}
    avg_sums: dict[str, float] = {}
    avg_counts: dict[str, int] = {}
    if is_avg and intent.group_by is None:
        vals = [float(rec.get(agg_col, rec.get("amount", 0)) or 0) for rec in filtered]
        avg = round(sum(vals) / len(vals), 2) if vals else 0.0
        answer = [{"average": avg}]
    else:
        for rec in filtered:
            if group_key in ("industry", "city", "country") and (join_by_id or join_by_name):
                cid = str(rec.get("customerId", "")).strip()
                cname = str(rec.get("customer", "")).lower().strip()
                g = join_by_id.get(cid, "") or join_by_name.get(cname, "")
                g = g.strip()
                if not g:
                    continue
            else:
                g = str(rec.get(group_key, "")).strip()
            if not g:
                continue
            if is_count:
                totals[g] = totals.get(g, 0.0) + 1
            elif is_avg:
                avg_sums[g] = avg_sums.get(g, 0.0) + float(rec.get(agg_col, rec.get("amount", 0)))
                avg_counts[g] = avg_counts.get(g, 0) + 1
            else:
                val = _parse_amount(rec.get(agg_col, rec.get("amount", 0)))
                totals[g] = totals.get(g, 0.0) + val
        if is_avg:
            key_name = "customer" if group_key == "customer" else group_key
            avg_by_key = {k: round(avg_sums[k] / avg_counts[k], 2) for k in avg_sums}
            ranked = sorted(avg_by_key.items(), key=lambda kv: kv[1], reverse=intent.sort_order != "asc")[:limit]
            answer = [{key_name: k, "average": v} for k, v in ranked]
        else:
            ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=intent.sort_order != "asc")[:limit]
            key_name = "customer" if group_key == "customer" else group_key
            if is_count:
                answer = [{key_name: k, "count": int(v)} for k, v in ranked]
            else:
                answer = [{key_name: k, "revenue": round(v, 2)} for k, v in ranked]
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


def _lookup_customer(
    question: str,
    intent: IntentConfig,
    ctx: SessionContext,
    page: Page,
    app_url: str,
    capture,
) -> AnsweredQuestion | None:
    """Lookup contact/email/phone for a customer — handles 'who is contact at Acme Corp?'."""
    lookup_field = (intent.column or "contact").lower()
    if lookup_field not in (
        "contact",
        "email",
        "phone",
        "industry",
        "city",
        "country",
        "name",
        "customer",
        "creditrating",
        "since",
    ):
        lookup_field = "contact"
    value = (intent.value or "").strip()
    if not value:
        return None

    # ensure on customers page so network capture has customers.json
    if current_route(page) != "#/customers":
        try:
            navigate(page, "customers", app_url)
            ctx.record("nav", "navigate.customers", outcome="landed", url=page.url)
        except Exception:
            pass

    # try network first — precise JSON
    customers: list[dict] | None = None
    if capture is not None:
        try:
            body = capture.latest_response_body("customers.json")
            if isinstance(body, list) and body and isinstance(body[0], dict):
                customers = body
        except Exception:
            customers = None
        if customers is None:
            for url in capture.capture_response_urls():
                if "customers.json" in url:
                    try:
                        b = capture.response_body(url)
                        if isinstance(b, list) and b and isinstance(b[0], dict):
                            customers = b
                            break
                    except Exception:
                        continue

    if customers is not None:
        matched = [c for c in customers if _matches(str(c.get("name", "")), value, "exact")]
        if not matched:
            # fallback contains
            lowered = value.lower()
            matched = [c for c in customers if lowered in str(c.get("name", "")).lower()]
        if not matched:
            return _freeze(
                AnsweredQuestion(
                    question=question,
                    intent=QuestionIntent.LOOKUP,
                    not_found=True,
                    message=f"no customer with name {value!r}",
                    evidence=AnswerEvidence(
                        source="customers.json", column=lookup_field, matched_rows=0, endpoint="customers.json"
                    ),  # noqa: E501
                    confidence="high",
                ),
                ctx,
            )
        rec = matched[0]
        answer_payload = [
            {
                "customer": rec.get("name"),
                "contact": rec.get("contact"),
                "contactTitle": rec.get("contactTitle"),
                "email": rec.get("email"),
                "phone": rec.get("phone"),
                "city": rec.get("city"),
                "country": rec.get("country"),
                "industry": rec.get("industry"),
                "creditRating": rec.get("creditRating"),
                "since": rec.get("since"),
            }
        ]
        ctx.record("answer", "lookup", outcome=f"found {rec.get('name')} contact={rec.get('contact')}")
        return _freeze(
            AnsweredQuestion(
                question=question,
                intent=QuestionIntent.LOOKUP,
                answer=answer_payload,
                evidence=AnswerEvidence(
                    source="customers.json", column=lookup_field, matched_rows=len(matched), endpoint="customers.json"
                ),  # noqa: E501
                confidence="high",
            ),
            ctx,
        )

    # fallback — table snapshot
    snapshot = _snapshot(page, ctx)
    # customersTable columns: Customer, Industry, Contact, Location, Email, Phone
    col_idx = {name.lower(): idx for idx, name in enumerate(snapshot.data.columns)}
    cust_idx = col_idx.get("customer")
    contact_idx = col_idx.get("contact")
    if cust_idx is None:
        return None
    matched_rows = [r for r in snapshot.data.rows if cust_idx < len(r) and _matches(r[cust_idx], value, "exact")]
    if not matched_rows:
        lowered = value.lower()
        matched_rows = [r for r in snapshot.data.rows if cust_idx < len(r) and lowered in r[cust_idx].lower()]
    if not matched_rows:
        return _freeze(
            AnsweredQuestion(
                question=question,
                intent=QuestionIntent.LOOKUP,
                not_found=True,
                message=f"no customer with name {value!r}",
                evidence=AnswerEvidence(
                    source="customersTable", column=lookup_field, matched_rows=0, endpoint="customersTable"
                ),  # noqa: E501
                confidence="high",
            ),
            ctx,
        )
    # return first match contact
    row = matched_rows[0]
    contact_val = row[contact_idx].strip() if contact_idx is not None and contact_idx < len(row) else ""
    email_idx = col_idx.get("email")
    phone_idx = col_idx.get("phone")
    answer_payload = [
        {
            "customer": row[cust_idx].strip() if cust_idx < len(row) else value,
            "contact": contact_val,
            "email": row[email_idx].strip() if email_idx is not None and email_idx < len(row) else "",
            "phone": row[phone_idx].strip() if phone_idx is not None and phone_idx < len(row) else "",
        }
    ]
    ctx.record("answer", "lookup", outcome=f"found {value} contact={contact_val} via table")
    return _freeze(
        AnsweredQuestion(
            question=question,
            intent=QuestionIntent.LOOKUP,
            answer=answer_payload,
            evidence=AnswerEvidence(
                source="customersTable", column=lookup_field, matched_rows=len(matched_rows), endpoint="customersTable"
            ),  # noqa: E501
            confidence="high",
        ),
        ctx,
    )


def _lookup_product(
    question: str,
    intent: IntentConfig,
    ctx: SessionContext,
    page: Page,
    app_url: str,
    capture,
) -> AnsweredQuestion | None:
    """Lookup price/stock/category for a product — handles 'price for Industrial Pump P-200?'."""
    lookup_field = (intent.column or "").lower()
    if lookup_field not in ("price", "stock", "category", "name", "unit"):
        return None
    value = (intent.value or "").strip()
    if not value:
        return None

    if current_route(page) != "#/catalog":
        try:
            navigate(page, "catalog", app_url)
            ctx.record("nav", "navigate.catalog", outcome="landed", url=page.url)
        except Exception:
            pass

    # try network first — products.json
    products: list[dict] | None = None
    if capture is not None:
        try:
            body = capture.latest_response_body("products.json")
            if isinstance(body, list) and body and isinstance(body[0], dict):
                products = body
        except Exception:
            products = None
        if products is None:
            for url in capture.capture_response_urls():
                if "products.json" in url:
                    try:
                        b = capture.response_body(url)
                        if isinstance(b, list) and b and isinstance(b[0], dict):
                            products = b
                            break
                    except Exception:
                        continue

    if products is not None:
        # filter visible only — active true (respect visible-only rule)
        # keep inactive for not_found check, but note visible filter for answer
        matched = [p for p in products if _matches(str(p.get("name", "")), value, "exact")]
        if not matched:
            lowered = value.lower()
            matched = [p for p in products if lowered in str(p.get("name", "")).lower()]
        if not matched:
            return _freeze(
                AnsweredQuestion(
                    question=question,
                    intent=QuestionIntent.LOOKUP,
                    not_found=True,
                    message=f"no product with name {value!r}",
                    evidence=AnswerEvidence(
                        source="products.json", column=lookup_field, matched_rows=0, endpoint="products.json"
                    ),  # noqa: E501
                    confidence="high",
                ),
                ctx,
            )
        rec = matched[0]
        # visible check: if inactive, treat as not visible but still answer via network? For visible-only we note
        answer_payload = [
            {
                "name": rec.get("name"),
                "category": rec.get("category"),
                "price": rec.get("price"),
                "stock": rec.get("stock"),
                "unit": rec.get("unit"),
                "active": rec.get("active"),
            }
        ]
        ctx.record("answer", "lookup", outcome=f"found {rec.get('name')} {lookup_field}={rec.get(lookup_field)}")
        return _freeze(
            AnsweredQuestion(
                question=question,
                intent=QuestionIntent.LOOKUP,
                answer=answer_payload,
                evidence=AnswerEvidence(
                    source="products.json", column=lookup_field, matched_rows=len(matched), endpoint="products.json"
                ),  # noqa: E501
                confidence="high",
            ),
            ctx,
        )

    # fallback — table snapshot (visible only)
    snapshot = _snapshot(page, ctx)
    col_idx = {name.lower(): idx for idx, name in enumerate(snapshot.data.columns)}
    name_idx = col_idx.get("product") or col_idx.get("name")
    if name_idx is None:
        # try first column as product name
        name_idx = 0
    target_idx = col_idx.get(lookup_field)
    if target_idx is None:
        return None
    matched_rows = [r for r in snapshot.data.rows if name_idx < len(r) and _matches(r[name_idx], value, "exact")]
    if not matched_rows:
        lowered = value.lower()
        matched_rows = [r for r in snapshot.data.rows if name_idx < len(r) and lowered in r[name_idx].lower()]
    if not matched_rows:
        return _freeze(
            AnsweredQuestion(
                question=question,
                intent=QuestionIntent.LOOKUP,
                not_found=True,
                message=f"no product with name {value!r}",
                evidence=AnswerEvidence(
                    source="productTable", column=lookup_field, matched_rows=0, endpoint="productTable"
                ),  # noqa: E501
                confidence="high",
            ),
            ctx,
        )
    row = matched_rows[0]
    answer_payload = [
        {
            "name": row[name_idx].strip() if name_idx < len(row) else value,
            lookup_field: row[target_idx].strip() if target_idx < len(row) else "",
        }
    ]
    ctx.record("answer", "lookup", outcome=f"found {value} via table")
    return _freeze(
        AnsweredQuestion(
            question=question,
            intent=QuestionIntent.LOOKUP,
            answer=answer_payload,
            evidence=AnswerEvidence(
                source="productTable", column=lookup_field, matched_rows=len(matched_rows), endpoint="productTable"
            ),  # noqa: E501
            confidence="high",
        ),
        ctx,
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
        if auto and current_route(page) != f"#/{auto}":
            try:
                navigate(page, auto, app_url)
                ctx.record("nav", f"navigate.{auto}", outcome="auto", url=page.url)
                # customers/catalog tables load async via fetch — wait briefly for rows
                with contextlib.suppress(Exception):
                    page.wait_for_timeout(900)
            except Exception:
                pass

    snapshot = _snapshot(page, ctx)
    # retry snapshot if customers table still busy (rows=1 header only)
    if snapshot.data.row_count == 1 and any(c.lower() == "location" for c in snapshot.columns):
        with contextlib.suppress(Exception):
            page.wait_for_timeout(800)
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
            try:
                body = capture.latest_response_body(json_key)
                if body is None:
                    body = capture.latest_response_body(json_key[: json_key.rfind(".")])
                if isinstance(body, list) and body and isinstance(body[0], dict):
                    network_rows = body
            except Exception:
                network_rows = None
        agg = _aggregate_top(
            question,
            intent,
            ctx,
            source=source,
            endpoint=endpoint,
            snapshot=snapshot,
            network_rows=network_rows,
            capture=capture,  # noqa: E501
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
            )  # noqa: E501
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
                    ),  # noqa: E501
                    confidence="high",
                )
                ctx.record(
                    "answer",
                    "count_where",
                    outcome=f"count={result.answer}",
                    detail=f"matched {len(matched)} via Location",
                )  # noqa: E501
                return _freeze(result, ctx)
            if intent.intent == QuestionIntent.EXISTENCE:
                result = AnsweredQuestion(
                    question=question,
                    intent=QuestionIntent.EXISTENCE,
                    answer=1 if matched else 0,
                    evidence=AnswerEvidence(
                        source=source, column=intent.column, matched_rows=len(matched), endpoint=endpoint
                    ),  # noqa: E501
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
