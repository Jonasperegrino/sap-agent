"""LLM slot for intent parsing (issue #647 extension).

Thin OpenAI-compatible client. No extra deps — uses stdlib
urllib so `uv add openai` is optional. Deterministic fallback: if no
key or call fails, caller keeps the rule-based IntentConfig.

Security: api key is SecretStr, never logged or added to trace.
"""

from __future__ import annotations

import contextlib
import json
import logging
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

from ..schemas import Config, IntentConfig, QuestionIntent

if TYPE_CHECKING:
    from ..context import SessionContext

logger = logging.getLogger(__name__)

KNOWN_STATUSES = ("Approved", "Pending", "Shipped", "Rejected", "Cancelled")

SYSTEM_PROMPT = """You are a Fiori intent parser. Map the user question to JSON.
Allowed intents: count_total, count_where, existence, aggregate, unsupported.
Fields:
- intent: one of allowed
- column: filter column or null (status|customer|amount|built|price|stock|category|unit|qty|name)
- value: filter value or null (e.g. "2025", "Approved", "Acme Corp")
- comparer: "exact" or "year" (year when value is YYYY)
- aggregation: for aggregate only: sum|avg|count or null
- aggregation_column: column to aggregate (e.g. amount) or null
- group_by: column to group by (e.g. customer) or null
- limit: int or null (e.g. 3 for top 3)
- sort_order: desc|asc
- follow_up: string for unsupported

Examples:
Q: "how many orders were built in 2026" -> {"intent":"count_where","column":"built","value":"2026"}
Q: "revenue of our top 3 clients last year?" (2026->last 2025)
  -> {"intent":"aggregate","column":"built","value":"2025","comparer":"year",
      "aggregation":"sum","aggregation_column":"amount","group_by":"customer","limit":3}
Q: "which customer spent the most"
  -> {"intent":"aggregate","column":null,"value":null,"aggregation":"sum",
      "aggregation_column":"amount","group_by":"customer","limit":3}
Q: "play jazz" -> {"intent":"unsupported","follow_up":"unsupported question type"}
Return JSON only, no markdown.
Known columns: status, customer, amount, built, price, stock, category, unit, qty, name.
"""


def _payload_openai(question: str, model: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }


def _post_json(url: str, headers: dict[str, str], body: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode())


def _extract_content(resp: dict[str, Any]) -> str:
    # openai: choices[0].message.content
    choices = resp.get("choices", [])
    if choices:
        return choices[0].get("message", {}).get("content", "") or ""
    return ""


def _parse_llm_json(text: str) -> IntentConfig | None:
    text = text.strip()
    # strip markdown fences if model wraps
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        raw = json.loads(text)
    except (ValueError, TypeError, AttributeError):
        return None
    # validate intent
    intent_raw = str(raw.get("intent", "unsupported")).lower()
    try:
        intent = QuestionIntent(intent_raw)
    except ValueError:
        intent = QuestionIntent.UNSUPPORTED
    # coerce limit
    limit = raw.get("limit")
    if limit is not None:
        try:
            limit = int(limit)
        except (ValueError, TypeError):
            limit = None
    return IntentConfig(
        intent=intent,
        column=raw.get("column"),
        value=raw.get("value"),
        comparer=raw.get("comparer") or "exact",
        follow_up=raw.get("follow_up") or "",
        aggregation=raw.get("aggregation"),
        aggregation_column=raw.get("aggregation_column"),
        group_by=raw.get("group_by"),
        limit=limit,
        sort_order=raw.get("sort_order") or "desc",
    )


def call_llm_for_intent(question: str, config: Config, ctx: SessionContext | None = None) -> IntentConfig | None:
    """Call LLM API to parse question. Returns IntentConfig or None on skip/fail."""
    if not config.has_llm():
        return None
    api_key = config.llm_api_key.get_secret_value() if config.llm_api_key is not None else ""
    if not api_key:
        return None

    base = config.llm_base_url.rstrip("/")
    model = config.llm_model

    # openai + compatible (openai, azure, local llm)
    url = f"{base}/chat/completions" if not base.endswith("/chat/completions") else base
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = _payload_openai(question, model)

    try:
        resp = _post_json(url, headers, payload, 15.0)
        content = _extract_content(resp)
        if not content:
            logger.warning("llm empty content")
            return None
        parsed = _parse_llm_json(content)
        if ctx is not None:
            ctx.record("reason", "llm.parse", outcome=parsed.intent.value if parsed else "parse_failed")
        if parsed and parsed.intent != QuestionIntent.UNSUPPORTED:
            logger.info(
                "llm parsed intent=%s col=%s agg=%s group=%s",
                parsed.intent.value,
                parsed.column,
                parsed.aggregation,
                parsed.group_by,
            )
        return parsed
    except urllib.error.HTTPError as exc:
        body = ""
        with contextlib.suppress(OSError, ValueError, AttributeError, TypeError):
            body = exc.read().decode()[:500]
        logger.warning("llm http %s: %s", exc.code, body[:200])
        if ctx is not None:
            ctx.record("reason", "llm.error", outcome=f"http_{exc.code}")
        return None
    except (OSError, TimeoutError, ValueError, KeyError, TypeError) as exc:  # timeout, json error
        logger.warning("llm call failed: %s", exc)
        if ctx is not None:
            ctx.record("reason", "llm.error", outcome="exception")
        return None
