"""Regression test source for the SO-1024 currency defect.

Pure string builder (no Streamlit or Playwright import) so the generated test is
unit-testable. The test asserts that the dashboard Total Value shows the same
figure as the order-amount footer. It fails while the currency mapping defect is
present and passes once the BRL order is converted at the demo rate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sap_agent.demo_case import CASE

if TYPE_CHECKING:
    from sap_agent.demo_case import DemoResult

APP_URL = "https://jonasperegrino.github.io/sap-fiori/"


def _eur(value: float) -> str:
    return f"€{value:,.2f}"


def regression_test_source(result: DemoResult | None = None) -> str:
    """Return a pytest + Playwright test, built from the investigation's figures."""
    case = result["case"] if result else CASE
    expected = _eur(case["order_rows_sum_eur"])
    actual = _eur(case["dashboard_total_eur"])
    order = case["order_id"]
    return f'''"""Regression: dashboard Total Value must equal the sum of order amounts.

Found while investigating {order} ({case["customer"]}, {case["customer_id"]}).
Fails while the currency mapping defect is present (dashboard shows {actual},
order amounts sum to {expected}).
"""

from playwright.sync_api import Page, expect

APP_URL = "{APP_URL}"


def test_total_value_matches_order_amounts(page: Page) -> None:
    page.goto(APP_URL)
    expected_total = "{expected}"

    # The footer under the order table states the sum of the displayed amounts.
    expect(page.get_by_text(f"Sum of amounts {{expected_total}}")).to_be_visible()

    # The Total Value KPI must show the same figure.
    total_tile = page.get_by_text("Total Value", exact=True).locator("xpath=..")
    expect(total_tile).to_contain_text(expected_total)
'''
