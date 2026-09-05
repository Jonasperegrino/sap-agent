"""Reason reference data (split from reason.py): columns, patterns, vocabularies.

Pure data, no logic. The parsers in reason.py match against these tables.
"""

from __future__ import annotations

import re

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

AGGREGATE_GROUPS = ("customer", "industry", "country", "city", "status", "category")
