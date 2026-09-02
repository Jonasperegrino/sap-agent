"""Shared fixtures and constants for sap_agent tests.

Demo credentials are a documented exception (AGENTS.md) and live here only.
All tests reference these constants instead of hardcoding values.
"""

from __future__ import annotations

from sap_agent.schemas import Config

APP_URL = "http://localhost:8080"
DEMO_USER = "demo"
DEMO_PASSWORD = "password123"
RETRY_BUDGET = 3


def demo_config(password: str | None = None, retry_budget: int = RETRY_BUDGET) -> Config:
    """Return a Config pre-filled with demo credentials."""
    return Config(
        app_url=APP_URL,
        username=DEMO_USER,
        password=password or DEMO_PASSWORD,
        retry_budget=retry_budget,
    )
