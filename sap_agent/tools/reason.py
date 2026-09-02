"""Question intent mapping (issue #647): rule-based, deterministic.

Translation of free-text questions into IntentConfig so the answer tool can
execute a reproducible lookup. LLM slot-in for richer parsing is documented in
docs/architecture.md; the rule-based version covers the MVP question classes.
"""

from __future__ import annotations

import re

from ..schemas import IntentConfig, QuestionIntent

#: known filterable columns across all pages (dashboard + catalog + history);
#: "order"/"product" are intentionally absent — entity words, not data columns
KNOWN_COLUMNS: tuple[str, ...] = (
    "status",
    "customer",
    "amount",
    "built",
    "price",
    "stock",
    "category",
    "unit",
    "quantity",
    "qty",
    "name",
    "contact",
    "email",
    "phone",
    "industry",
    "city",
    "country",
)

#: contact lookup triggers — "who is contact at Acme Corp?"
CONTACT_LOOKUP_RE = re.compile(
    r"(?:contact|email|phone).*?(?:at|for|of)\s+([A-Za-z0-9][\w\s\.\-]*?)\s*[?.!]?$",
    re.IGNORECASE,
)
WHO_CONTACT_RE = re.compile(
    r"who.*?(?:contact|email|phone).*?(?:at|for|of)\s+([A-Za-z0-9][\w\s\.\-]*?)\s*[?.!]?$",
    re.IGNORECASE,
)

#: value words that imply the status column (order statuses on the PoC)
STATUS_VALUE_WORDS: tuple[str, ...] = ("approved", "pending", "shipped", "rejected", "cancelled")

COUNT_TOTAL_PATTERNS = (
    re.compile(r"how many (?:orders?|rows?|entries|sales orders?|records|products?|customers?|items?)\b"),
    re.compile(r"\btotal (?:orders?|rows?|entries|products?|customers?|items?)\b"),
    re.compile(r"count (?:of )?(?:orders?|rows?|entries|products?|customers?|items?)\b"),
    re.compile(r"number of (?:orders?|rows?|entries|sales orders?|records|products?|customers?|items?)\b"),
)

EXISTENCE_PATTERNS = (
    re.compile(r"\bis there (?:any|an|a)\b"),
    re.compile(r"\bdoes (?:any|the)\b.*\bexist\b"),
    re.compile(r"\bare there (?:any|orders)\b"),
)

# greedy prefix forces the LAST separator occurrence so values never absorb a
# leading preposition ("are in stock" must yield "stock", not "in stock")
COUNT_WHERE_VALUE = re.compile(r".*\b(?:with|where|for|by|built in|in|from|are|is)\s+([\w€. ,-]+?)\s*[?.!]?$")
YEAR_ONLY = re.compile(r"^(19|20)\d{2}$")

#: value shapes that imply the customer column (company-name heuristics)
CUSTOMER_SUFFIX_RE = re.compile(
    r"\b(corp|gmbh|ltd|llc|supply|trading|energy|industries|logistics|parts|technologies?)$"
)
KNOWN_CUSTOMERS: tuple[str, ...] = (
    "acme corp",
    "globaltech",
    "europarts",
    "nordic supply",
    "iberia trading",
    "atlas industries",
    "eastline logistics",
    "bluewave energy",
    "helios manufacturing",
    "quantum robotics",
)

KNOWN_COUNTRIES: tuple[str, ...] = (
    "germany",
    "france",
    "norway",
    "spain",
    "switzerland",
    "poland",
    "ireland",
    "netherlands",
)

KNOWN_CITIES: tuple[str, ...] = (
    "berlin",
    "munich",
    "paris",
    "oslo",
    "madrid",
    "zurich",
    "warsaw",
    "dublin",
    "valencia",
    "eindhoven",
)

KNOWN_INDUSTRIES: tuple[str, ...] = (
    "manufacturing",
    "information technology",
    "automotive",
    "logistics",
    "wholesale trade",
    "industrial machinery",
    "transportation",
    "energy",
    "robotics",
)

KNOWN_PRODUCTS: tuple[str, ...] = (
    "industrial pump p-200",
    "hydraulic valve hv-5",
    "servo motor sm-90",
    "plc controller plc-x1",
    "thermal sensor ts-100",
    "data logger dl-4",
    "lubricant oil 20l",
    "filter cartridge fc-7",
    "conveyor belt cb-30",
    "maintenance service day",
    "calibration service",
    "edge gateway eg-2",
    "safety gloves size l",
    "vibration sensor vs-3",
    "training workshop day",
)


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
    """Rule first, LLM fallback (openai/anthropic compatible via SAP_AGENT_LLM_API_KEY).

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
    except Exception:
        pass
    return base


def _parse_contact_lookup(question: str) -> IntentConfig | None:
    lowered = question.lower()
    if not any(k in lowered for k in ("contact", "email", "phone", "city", "country", "industry")):
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
    ):  # noqa: E501, SIM102
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
            if "email" in lowered:
                column = "email"
            elif "phone" in lowered:
                column = "phone"
            elif "city" in lowered:
                column = "city"
            elif "country" in lowered:
                column = "country"
            elif "industry" in lowered:
                column = "industry"
            else:
                column = "contact"
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
            if "email" in lowered:
                column = "email"
            elif "phone" in lowered:
                column = "phone"
            elif "city" in lowered:
                column = "city"
            elif "country" in lowered:
                column = "country"
            elif "industry" in lowered:
                column = "industry"
            else:
                column = "contact"
            return IntentConfig(intent=QuestionIntent.LOOKUP, column=column, value=raw, comparer="exact")
    # fallback 2: use LAST separator heuristic (same as COUNT_WHERE) when contact present
    m = COUNT_WHERE_VALUE.search(question.strip())
    if m:
        raw_value = m.group(1).strip().strip("?.!")
        raw_value = re.sub(r"^(?:our|the|my)\s+", "", raw_value, flags=re.IGNORECASE).strip()
        if raw_value and (_looks_like_customer(raw_value) or len(raw_value.split()) <= 3):
            if "email" in lowered:
                column = "email"
            elif "phone" in lowered:
                column = "phone"
            elif "city" in lowered:
                column = "city"
            elif "country" in lowered:
                column = "country"
            elif "industry" in lowered:
                column = "industry"
            else:
                column = "contact"
            return IntentConfig(intent=QuestionIntent.LOOKUP, column=column, value=raw_value, comparer="exact")
    return None


def _parse_amount_orders(question: str) -> IntentConfig | None:
    lowered = question.lower()
    has_amount = "amount" in lowered or "revenue" in lowered
    has_orders = "orders" in lowered or "order" in lowered

    # orders by customer/industry/country/city — count per group
    for grp in ("customer", "industry", "country", "city"):
        if has_orders and (f"by {grp}" in lowered or f"per {grp}" in lowered):
            return IntentConfig(
                intent=QuestionIntent.AGGREGATE,
                aggregation="count",
                group_by=grp,
                limit=10,
            )
    # amount/revenue by customer/industry/country — sum per group
    for grp in ("customer", "industry", "country", "city"):
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
        # try known customer first
        for cust in KNOWN_CUSTOMERS:
            if cust in lowered:
                idx = lowered.find(cust)
                raw = question[idx : idx + len(cust)].strip().strip("?.!")
                if not raw:
                    raw = cust
                return IntentConfig(
                    intent=QuestionIntent.AGGREGATE,
                    aggregation="sum",
                    aggregation_column="amount",
                    column="customer",
                    value=raw.strip(),
                    comparer="exact",
                    group_by="customer",
                )
        for country in KNOWN_COUNTRIES:
            if country in lowered:
                idx = lowered.find(country)
                raw = question[idx : idx + len(country)].strip().strip("?.!")
                if not raw:
                    raw = country
                # preserve original case: find with title
                raw = question[idx : idx + len(raw)].strip() if raw else country
                return IntentConfig(
                    intent=QuestionIntent.AGGREGATE,
                    aggregation="sum",
                    aggregation_column="amount",
                    column="country",
                    value=raw.strip(),
                    comparer="exact",
                    group_by="country",
                )
        for ind in KNOWN_INDUSTRIES:
            if ind in lowered:
                idx = lowered.find(ind)
                raw = question[idx : idx + len(ind)].strip().strip("?.!")
                if not raw:
                    raw = ind
                return IntentConfig(
                    intent=QuestionIntent.AGGREGATE,
                    aggregation="sum",
                    aggregation_column="amount",
                    column="industry",
                    value=raw.strip(),
                    comparer="exact",
                    group_by="industry",
                )
        # fallback via LAST separator
        m = COUNT_WHERE_VALUE.search(question.strip())
        if m:
            raw_value = m.group(1).strip().strip("?.!")
            raw_value = re.sub(r"^(?:our|the|my)\s+", "", raw_value, flags=re.IGNORECASE).strip()
            if raw_value and _looks_like_customer(raw_value):
                return IntentConfig(
                    intent=QuestionIntent.AGGREGATE,
                    aggregation="sum",
                    aggregation_column="amount",
                    column="customer",
                    value=raw_value,
                    comparer="exact",
                    group_by="customer",
                )
            # also check if raw is country/industry
            if raw_value.lower() in KNOWN_COUNTRIES:
                return IntentConfig(
                    intent=QuestionIntent.AGGREGATE,
                    aggregation="sum",
                    aggregation_column="amount",
                    column="country",
                    value=raw_value,
                    comparer="exact",
                    group_by="country",
                )
            if raw_value.lower() in KNOWN_INDUSTRIES:
                return IntentConfig(
                    intent=QuestionIntent.AGGREGATE,
                    aggregation="sum",
                    aggregation_column="amount",
                    column="industry",
                    value=raw_value,
                    comparer="exact",
                    group_by="industry",
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
                    m2 = re.search(r"credit rating\s+([ABC])\b", value, re.IGNORECASE)
                    if m2:
                        column = "creditRating"
                        value = m2.group(1).upper()
                    elif value.strip().lower() in {"a", "b", "c"}:
                        column = "creditRating"
                    elif "credit rating" in value.lower():
                        parts = value.strip().split()
                        if parts and parts[-1].lower() in {"a", "b", "c"}:
                            column = "creditRating"
                            value = parts[-1].upper()
                        else:
                            column = "creditRating"
                            # keep value as is, will be A/B/C after stripping prefix below
                            if value.lower().startswith("credit rating"):
                                value = value[len("credit rating") :].strip()
                if column is None:
                    # special case: how many customers from Germany → value Germany → country
                    # already handled via _infer, else default to built for date-like
                    if "customers" in lowered and _infer_customer_column(value):
                        column = _infer_customer_column(value)  # type: ignore[assignment]
                    else:
                        column = "built"
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
        m = COUNT_WHERE_VALUE.search(question.strip())
        if m:
            raw = m.group(1).strip().strip("?.!")
            raw = re.sub(r"^(?:our|the|my)\s+", "", raw, flags=re.IGNORECASE).strip()
            if raw and _looks_like_customer(raw):
                return IntentConfig(intent=QuestionIntent.COUNT_WHERE, column="customer", value=raw, comparer="exact")
        for cust in KNOWN_CUSTOMERS:
            if cust in lowered:
                idx = lowered.find(cust)
                raw = question[idx : idx + len(cust)].strip().strip("?.!")
                return IntentConfig(
                    intent=QuestionIntent.COUNT_WHERE, column="customer", value=raw or cust, comparer="exact"
                )

    return IntentConfig(intent=QuestionIntent.UNSUPPORTED, follow_up="unsupported question type")
