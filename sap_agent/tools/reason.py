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
)

#: value words that imply the status column (order statuses on the PoC)
STATUS_VALUE_WORDS: tuple[str, ...] = ("approved", "pending", "shipped", "rejected", "cancelled")

COUNT_TOTAL_PATTERNS = (
    re.compile(r"how many (?:orders|rows|entries|sales orders|records|products|customers|items)\b"),
    re.compile(r"\btotal (?:orders|rows|entries|products|customers|items)\b"),
    re.compile(r"count (?:of )?(?:orders|rows|entries|products|customers|items)\b"),
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
)


def _looks_like_customer(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered in KNOWN_CUSTOMERS or bool(CUSTOMER_SUFFIX_RE.search(lowered))


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


def parse_question(question: str) -> IntentConfig:
    lowered = question.lower().strip()
    if not lowered:
        return IntentConfig(intent=QuestionIntent.UNSUPPORTED, follow_up="empty question")

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
                if column is None and _looks_like_customer(value):
                    column = "customer"
                if column is None:
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

    return IntentConfig(intent=QuestionIntent.UNSUPPORTED, follow_up="unsupported question type")
