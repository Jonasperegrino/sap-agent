"""Question intent mapping (issue #647): rule-based, deterministic.

Translation of free-text questions into IntentConfig so the answer tool can
execute a reproducible lookup. LLM slot-in for richer parsing is documented in
docs/architecture.md; the rule-based version covers the MVP question classes.
"""

from __future__ import annotations

import logging
import re

from ..schemas import IntentConfig, QuestionIntent
from .reason_data import (
    AGGREGATE_GROUPS,
    CONTACT_LOOKUP_RE,
    COUNT_TOTAL_PATTERNS,
    COUNT_WHERE_VALUE,
    CUSTOMER_SUFFIX_RE,
    EXISTENCE_PATTERNS,
    KNOWN_CITIES,
    KNOWN_COLUMNS,
    KNOWN_COUNTRIES,
    KNOWN_CUSTOMERS,
    KNOWN_INDUSTRIES,
    KNOWN_PRODUCTS,
    STATUS_VALUE_WORDS,
    WHO_CONTACT_RE,
    YEAR_ONLY,
)

logger = logging.getLogger(__name__)


def _looks_like_customer(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered in KNOWN_CUSTOMERS or bool(CUSTOMER_SUFFIX_RE.search(lowered))


def _looks_like_product(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered in KNOWN_PRODUCTS or any(p in lowered for p in KNOWN_PRODUCTS)


def _infer_customer_column(value: str) -> str | None:
    v = value.strip().lower()
    if v in KNOWN_COUNTRIES:
        return "country"
    if v in KNOWN_CITIES:
        return "city"
    if v in KNOWN_INDUSTRIES:
        return "industry"
    if v in {"a", "b", "c"}:
        # creditRating single letter, but avoid false positives
        return None
    return None


def _extract_column(question: str, value: str) -> str | None:
    lowered = question.lower()
    for col in KNOWN_COLUMNS:
        if re.search(rf"\b{re.escape(col)}\b", lowered):
            return col
    # "is there any approved order" — no column keyword, but the value word
    # ("approved") implies the status column
    banned = {"order", "orders"}
    words = {w for w in re.findall(r"\b[a-z]+\b", lowered) if w not in banned}
    if words & set(STATUS_VALUE_WORDS):
        return "status"
    if YEAR_ONLY.match(value.strip()):
        return "built"
    return None


def parse_question_with_llm(
    question: str,
    config=None,
    ctx=None,
) -> IntentConfig:
    """Rule first, LLM fallback (openai-compatible via SAP_AGENT_LLM_API_KEY).

    Keeps deterministic core — LLM only fires when rule returns UNSUPPORTED or
    empty, and result is validated into IntentConfig. No key ever enters trace.
    """
    base = parse_question(question)
    if base.intent != QuestionIntent.UNSUPPORTED:
        return base
    if config is None or not getattr(config, "has_llm", lambda: False)():
        return base
    try:
        from .llm import call_llm_for_intent

        llm_cfg = call_llm_for_intent(question, config, ctx)
        if llm_cfg is not None and llm_cfg.intent != QuestionIntent.UNSUPPORTED:
            return llm_cfg
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, TimeoutError) as exc:
        logger.debug("llm fallback skipped: %s", exc)
    return base


def _contact_column(lowered: str) -> str:
    for key, col in (
        ("email", "email"),
        ("phone", "phone"),
        ("city", "city"),
        ("country", "country"),
        ("industry", "industry"),
        ("credit", "creditRating"),
        ("since", "since"),
    ):
        if key in lowered:
            return col
    return "contact"


def _parse_contact_lookup(question: str) -> IntentConfig | None:
    lowered = question.lower()
    if not any(k in lowered for k in ("contact", "email", "phone", "city", "country", "industry", "credit", "since")):
        return None
    # don't hijack count/aggregate questions
    if any(p.search(lowered) for p in COUNT_TOTAL_PATTERNS):
        return None
    if (
        any(k in lowered for k in ("revenue", "amount"))
        and any(f"by {g}" in lowered for g in ("industry", "country", "city", "customer"))
        or (
            ("orders by" in lowered or "order by" in lowered)
            and any(f"by {g}" in lowered for g in ("customer", "industry", "country", "city"))
        )
    ):
        return None
    # try WHO pattern first, then generic contact pattern
    for pat in (WHO_CONTACT_RE, CONTACT_LOOKUP_RE):
        m = pat.search(question.strip())
        if m:
            raw_value = m.group(1).strip().strip("?.!")
            # strip leading determiners
            raw_value = re.sub(r"^(?:our|the|my)\s+", "", raw_value, flags=re.IGNORECASE).strip()
            if not raw_value:
                continue
            column = _contact_column(lowered)
            return IntentConfig(
                intent=QuestionIntent.LOOKUP,
                column=column,
                value=raw_value,
                comparer="exact",
            )
    # fallback 1: known customer name appears anywhere when contact is mentioned
    for cust in KNOWN_CUSTOMERS:
        if cust in lowered:
            idx = lowered.find(cust)
            # preserve original casing from question if possible
            raw = question[idx : idx + len(cust)].strip().strip("?.!")
            if not raw:
                raw = cust
            # title-case for display: Acme Corp vs acme corp
            raw = raw.strip()
            column = _contact_column(lowered)
            return IntentConfig(intent=QuestionIntent.LOOKUP, column=column, value=raw, comparer="exact")
    # fallback 2: use LAST separator heuristic (same as COUNT_WHERE) when contact present
    m = COUNT_WHERE_VALUE.search(question.strip())
    if m:
        raw_value = m.group(1).strip().strip("?.!")
        raw_value = re.sub(r"^(?:our|the|my)\s+", "", raw_value, flags=re.IGNORECASE).strip()
        if raw_value and _looks_like_customer(raw_value):
            column = _contact_column(lowered)
            return IntentConfig(intent=QuestionIntent.LOOKUP, column=column, value=raw_value, comparer="exact")
    return None


def _agg_sum(column: str, value: str) -> IntentConfig:
    return IntentConfig(
        intent=QuestionIntent.AGGREGATE,
        aggregation="sum",
        aggregation_column="amount",
        column=column,
        value=value,
        comparer="exact",
        group_by=column,
    )


def _known_word_raw(question: str, lowered: str, word: str) -> str:
    """Slice the question at the known word, preserving original casing."""
    idx = lowered.find(word)
    return question[idx : idx + len(word)].strip().strip("?.!") or word


def _count_where(column: str, value: str) -> IntentConfig:
    return IntentConfig(intent=QuestionIntent.COUNT_WHERE, column=column, value=value, comparer="exact")


def _last_value(question: str) -> str:
    """Value after the LAST by/for/of/from separator, determiners stripped."""
    m = COUNT_WHERE_VALUE.search(question.strip())
    if not m:
        return ""
    raw = m.group(1).strip().strip("?.!")
    return re.sub(r"^(?:our|the|my)\s+", "", raw, flags=re.IGNORECASE).strip()


_CREDIT_LETTERS = {"a", "b", "c"}


def _parse_credit_rating(value: str) -> tuple[str | None, str]:
    """Extract a credit-rating letter from a raw value; (None, value) when absent."""
    m = re.search(r"credit rating\s+([ABC])\b", value, re.IGNORECASE)
    if m:
        return "creditRating", m.group(1).upper()
    if value.strip().lower() in _CREDIT_LETTERS:
        return "creditRating", value
    if "credit rating" in value.lower():
        parts = value.strip().split()
        if parts and parts[-1].lower() in _CREDIT_LETTERS:
            return "creditRating", parts[-1].upper()
        return "creditRating", value[len("credit rating") :].strip() if value.lower().startswith(
            "credit rating"
        ) else value
    return None, value


def _parse_amount_orders(question: str) -> IntentConfig | None:
    lowered = question.lower()
    has_amount = "amount" in lowered or "revenue" in lowered
    has_orders = "orders" in lowered or "order" in lowered

    # orders by customer/industry/country/city/status — count per group
    for grp in ("customer", "industry", "country", "city", "status"):
        if has_orders and (f"by {grp}" in lowered or f"per {grp}" in lowered):
            return IntentConfig(
                intent=QuestionIntent.AGGREGATE,
                aggregation="count",
                group_by=grp,
                limit=10,
            )
    # amount/revenue by customer/industry/country/status — sum per group
    for grp in ("customer", "industry", "country", "city", "status"):
        if has_amount and (f"by {grp}" in lowered or f"per {grp}" in lowered):
            return IntentConfig(
                intent=QuestionIntent.AGGREGATE,
                aggregation="sum",
                aggregation_column="amount",
                group_by=grp,
                limit=10,
            )
    # total amount for specific customer/industry/country — sum for that group
    if has_amount and ("for" in lowered or "of" in lowered):
        for column, vocab in (
            ("customer", KNOWN_CUSTOMERS),
            ("country", KNOWN_COUNTRIES),
            ("industry", KNOWN_INDUSTRIES),
        ):
            for word in vocab:
                if word in lowered:
                    return _agg_sum(column, _known_word_raw(question, lowered, word).strip())
        # fallback via LAST separator
        m = COUNT_WHERE_VALUE.search(question.strip())
        if m:
            raw_value = m.group(1).strip().strip("?.!")
            raw_value = re.sub(r"^(?:our|the|my)\s+", "", raw_value, flags=re.IGNORECASE).strip()
            if raw_value and _looks_like_customer(raw_value):
                return _agg_sum("customer", raw_value)
            # also check if raw is country/industry
            if raw_value.lower() in KNOWN_COUNTRIES:
                return _agg_sum("country", raw_value)
            if raw_value.lower() in KNOWN_INDUSTRIES:
                return _agg_sum("industry", raw_value)
    return None


def _parse_avg(question: str) -> IntentConfig | None:
    lowered = question.lower()
    if "average" not in lowered and "avg" not in lowered:
        return None
    if "price" in lowered:
        agg_col = "price"
    elif "stock" in lowered:
        agg_col = "stock"
    else:
        agg_col = "amount"
    group_by: str | None = None
    for grp in AGGREGATE_GROUPS:
        if f"by {grp}" in lowered:
            group_by = grp
            break
    return IntentConfig(
        intent=QuestionIntent.AGGREGATE,
        aggregation="avg",
        aggregation_column=agg_col,
        group_by=group_by,
    )


def _parse_aggregate_count_by(question: str) -> IntentConfig | None:
    lowered = question.lower()
    # "how many customers by <group>" should route to customers page, not aggregate sales rows
    if "customers" in lowered:
        for grp in AGGREGATE_GROUPS:
            if f"by {grp}" in lowered:
                match = COUNT_WHERE_VALUE.search(question.strip())
                value = match.group(1).strip() if match else grp
                return IntentConfig(
                    intent=QuestionIntent.COUNT_WHERE,
                    column=grp,
                    value=value,
                    comparer="exact",
                )
        return None
    if not any(p.search(lowered) for p in COUNT_TOTAL_PATTERNS):
        return None
    for grp in AGGREGATE_GROUPS:
        if f"by {grp}" in lowered:
            return IntentConfig(
                intent=QuestionIntent.AGGREGATE,
                aggregation="count",
                group_by=grp,
                limit=10,
            )
    return None


def _parse_product_lookup(question: str) -> IntentConfig | None:
    lowered = question.lower()
    has_price = "price" in lowered
    has_stock = "stock" in lowered
    if not has_price and not has_stock:
        return None
    # try known product name first
    for prod in KNOWN_PRODUCTS:
        if prod in lowered:
            idx = lowered.find(prod)
            raw = question[idx : idx + len(prod)].strip().strip("?.!")
            if not raw:
                raw = prod
            col = "price" if has_price else "stock"
            # if both, prefer which appears closer to value? keep price priority
            if has_price and has_stock:
                # decide by keyword proximity
                p_idx = lowered.find("price")
                s_idx = lowered.find("stock")
                # choose closer to product name position
                prod_idx = idx
                col = "price" if abs(p_idx - prod_idx) < abs(s_idx - prod_idx) else "stock"
            return IntentConfig(intent=QuestionIntent.LOOKUP, column=col, value=raw.strip(), comparer="exact")
    # fallback via LAST separator
    m = COUNT_WHERE_VALUE.search(question.strip())
    if m:
        raw_value = m.group(1).strip().strip("?.!")
        raw_value = re.sub(r"^(?:our|the|my)\s+", "", raw_value, flags=re.IGNORECASE).strip()
        if raw_value and _looks_like_product(raw_value):
            col = "price" if has_price else "stock"
            return IntentConfig(intent=QuestionIntent.LOOKUP, column=col, value=raw_value, comparer="exact")
    return None


def parse_question(question: str) -> IntentConfig:
    lowered = question.lower().strip()
    if not lowered:
        return IntentConfig(intent=QuestionIntent.UNSUPPORTED, follow_up="empty question")

    # customer contact lookup — must run before COUNT patterns so "who is contact at Acme Corp"
    # doesn't fall through to unsupported
    contact_cfg = _parse_contact_lookup(question)
    if contact_cfg is not None:
        return contact_cfg

    # average / avg aggregate — must run before _parse_amount_orders so "average amount by X"
    # returns avg instead of sum
    avg_cfg = _parse_avg(question)
    if avg_cfg is not None:
        return avg_cfg

    # "how many <noun> by <group>" → AGGREGATE count, before COUNT_TOTAL_PATTERNS hijacks
    agg_count_cfg = _parse_aggregate_count_by(question)
    if agg_count_cfg is not None:
        return agg_count_cfg

    # amount/orders by customer — deterministic aggregate without LLM
    amount_cfg = _parse_amount_orders(question)
    if amount_cfg is not None:
        return amount_cfg

    product_cfg = _parse_product_lookup(question)
    if product_cfg is not None:
        return product_cfg

    for pattern in EXISTENCE_PATTERNS:
        if pattern.search(lowered):
            any_match = re.search(r"\bany\s+(\w+)\s+\w+s?\b", question, flags=re.IGNORECASE)
            value = any_match.group(1) if any_match else ""
            if not value:
                match = COUNT_WHERE_VALUE.search(question.strip())
                value = match.group(1).strip() if match else ""
            column = _extract_column(lowered, value)
            if column:
                return IntentConfig(
                    intent=QuestionIntent.EXISTENCE,
                    column=column,
                    value=value or None,
                    comparer="year" if YEAR_ONLY.match(value) else "exact",
                )
            return IntentConfig(intent=QuestionIntent.EXISTENCE, follow_up="which column to check?")

    for pattern in COUNT_TOTAL_PATTERNS:
        if pattern.search(lowered):
            # "how many orders WERE BUILT IN 2026" → COUNT_WHERE by date
            match = COUNT_WHERE_VALUE.search(question.strip())
            if match:
                value = match.group(1).strip()
                if value.lower() == "there":
                    return IntentConfig(intent=QuestionIntent.COUNT_TOTAL, comparer="exact")
                column = _extract_column(lowered, value)
                if column is None:
                    inferred = _infer_customer_column(value)
                    if inferred:
                        column = inferred
                if column is None and _looks_like_customer(value):
                    column = "customer"
                # credit rating: "credit rating A" or "A" with credit keyword
                if column is None and "credit" in lowered:
                    column, value = _parse_credit_rating(value)
                if column is None:
                    # special case: how many customers from Germany → value Germany → country
                    # already handled via _infer, else default to built for date-like
                    inferred = _infer_customer_column(value)
                    column = inferred if "customers" in lowered and inferred else "built"
                if column and value.lower().startswith(column.lower()):
                    value = value[len(column) :].strip()
                if not value:
                    return IntentConfig(intent=QuestionIntent.COUNT_TOTAL, comparer="exact")
                return IntentConfig(
                    intent=QuestionIntent.COUNT_WHERE,
                    column=column,
                    value=value,
                    comparer="year" if YEAR_ONLY.match(value) else "exact",
                )
            return IntentConfig(intent=QuestionIntent.COUNT_TOTAL, comparer="exact")

    if "find" in lowered or "look up" in lowered:
        return IntentConfig(
            intent=QuestionIntent.LOOKUP,
            column=_extract_column(lowered, ""),
            follow_up="say which field to look up?",
        )

    # bare "orders by Acme Corp" without how many/number of — treat as count
    if ("orders" in lowered or "order" in lowered) and any(
        sep in lowered for sep in (" by ", " for ", " of ", " from ")
    ):
        raw = _last_value(question)
        if raw and _looks_like_customer(raw):
            return _count_where("customer", raw)
        for cust in KNOWN_CUSTOMERS:
            if cust in lowered:
                return _count_where("customer", _known_word_raw(question, lowered, cust) or cust)

    # bare "customers from Germany" without how many — treat as count
    if "customers" in lowered and any(sep in lowered for sep in (" from ", " in ", " with ", " of ", " for ", " by ")):
        raw = _last_value(question)
        if raw:
            for col in ("industry", "country", "city", "credit rating"):
                if raw.lower().startswith(col):
                    raw = raw[len(col) :].strip()
                    break
            inferred = _infer_customer_column(raw)
            if inferred:
                return _count_where(inferred, raw)
            if _looks_like_customer(raw):
                return _count_where("customer", raw)
            if raw.lower() in _CREDIT_LETTERS and "credit" in lowered:
                return _count_where("creditRating", raw.upper())
            for col, vocab in (
                ("country", KNOWN_COUNTRIES),
                ("city", KNOWN_CITIES),
                ("industry", KNOWN_INDUSTRIES),
            ):
                if raw.lower() in vocab:
                    return _count_where(col, raw)
        for col, vocab in (
            ("country", KNOWN_COUNTRIES),
            ("city", KNOWN_CITIES),
            ("industry", KNOWN_INDUSTRIES),
        ):
            for val in vocab:
                if val in lowered:
                    return _count_where(
                        _infer_customer_column(val) or col, _known_word_raw(question, lowered, val) or val
                    )
        # credit rating bare
        if "credit" in lowered:
            m2 = re.search(r"credit rating\s+([ABC])\b", lowered, re.IGNORECASE)
            if m2:
                return _count_where("creditRating", m2.group(1).upper())

    return IntentConfig(intent=QuestionIntent.UNSUPPORTED, follow_up="unsupported question type")
