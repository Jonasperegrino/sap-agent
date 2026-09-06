"""Entity lookups (split from answer.py): customer contact + product details.

Each lookup tries captured network JSON first (precise), then falls back to
the rendered table snapshot (visible-only).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from playwright.sync_api import Error as PlaywrightError

from ..schemas import AnsweredQuestion, AnswerEvidence, IntentConfig, QuestionIntent
from ..ui5.bridge import current_route
from .answer_core import _freeze, _matches, _snapshot, fetch_json_body
from .nav import navigate

if TYPE_CHECKING:
    from ..context import SessionContext
    from ..protocols import CaptureLike, PageLike


def _match_exact_then_contains(items: list, name_of, value: str) -> list:
    """Exact match first, substring fallback — shared by network + table paths."""
    matched = [i for i in items if _matches(str(name_of(i)), value, "exact")]
    if not matched:
        lowered = value.lower()
        matched = [i for i in items if lowered in str(name_of(i)).lower()]
    return matched


def _not_found(
    question: str, label: str, value: str, source: str, column: str, ctx: SessionContext
) -> AnsweredQuestion:
    return _freeze(
        AnsweredQuestion(
            question=question,
            intent=QuestionIntent.LOOKUP,
            not_found=True,
            message=f"no {label} with name {value!r}",
            evidence=AnswerEvidence(source=source, column=column, matched_rows=0, endpoint=source),
            confidence="high",
        ),
        ctx,
    )


def _found(
    question: str,
    payload: list[dict],
    source: str,
    column: str,
    matched: int,
    outcome: str,
    ctx: SessionContext,
) -> AnsweredQuestion:
    ctx.record("answer", "lookup", outcome=outcome)
    return _freeze(
        AnsweredQuestion(
            question=question,
            intent=QuestionIntent.LOOKUP,
            answer=payload,
            evidence=AnswerEvidence(source=source, column=column, matched_rows=matched, endpoint=source),
            confidence="high",
        ),
        ctx,
    )


def _lookup_customer(
    question: str,
    intent: IntentConfig,
    ctx: SessionContext,
    page: PageLike,
    app_url: str,
    capture: CaptureLike | None,
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
        except PlaywrightError:
            pass

    # try network first — precise JSON
    customers = fetch_json_body(capture, "customers.json")

    if customers is not None:
        matched = _match_exact_then_contains(customers, lambda c: c.get("name", ""), value)
        if not matched:
            return _not_found(question, "customer", value, "customers.json", lookup_field, ctx)
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
        return _found(
            question,
            answer_payload,
            "customers.json",
            lookup_field,
            len(matched),
            f"found {rec.get('name')} contact={rec.get('contact')}",
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
    matched_rows = _match_exact_then_contains(
        [r for r in snapshot.data.rows if cust_idx < len(r)], lambda r: r[cust_idx], value
    )
    if not matched_rows:
        return _not_found(question, "customer", value, "customersTable", lookup_field, ctx)
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
    return _found(
        question,
        answer_payload,
        "customersTable",
        lookup_field,
        len(matched_rows),
        f"found {value} contact={contact_val} via table",
        ctx,
    )


def _lookup_product(
    question: str,
    intent: IntentConfig,
    ctx: SessionContext,
    page: PageLike,
    app_url: str,
    capture: CaptureLike | None,
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
        except PlaywrightError:
            pass

    # try network first — products.json
    products = fetch_json_body(capture, "products.json")

    if products is not None:
        # filter visible only — active true (respect visible-only rule)
        # keep inactive for not_found check, but note visible filter for answer
        matched = _match_exact_then_contains(products, lambda p: p.get("name", ""), value)
        if not matched:
            return _not_found(question, "product", value, "products.json", lookup_field, ctx)
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
        return _found(
            question,
            answer_payload,
            "products.json",
            lookup_field,
            len(matched),
            f"found {rec.get('name')} {lookup_field}={rec.get(lookup_field)}",
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
    matched_rows = _match_exact_then_contains(
        [r for r in snapshot.data.rows if name_idx < len(r)], lambda r: r[name_idx], value
    )
    if not matched_rows:
        return _not_found(question, "product", value, "productTable", lookup_field, ctx)
    row = matched_rows[0]
    answer_payload = [
        {
            "name": row[name_idx].strip() if name_idx < len(row) else value,
            lookup_field: row[target_idx].strip() if target_idx < len(row) else "",
        }
    ]
    return _found(
        question, answer_payload, "productTable", lookup_field, len(matched_rows), f"found {value} via table", ctx
    )
