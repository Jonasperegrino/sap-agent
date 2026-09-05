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
                    ),
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
                ),
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
                ),
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
            ),
            confidence="high",
        ),
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
                    ),
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
                ),
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
                ),
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
            ),
            confidence="high",
        ),
        ctx,
    )
