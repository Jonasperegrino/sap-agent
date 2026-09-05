"""Unit tests for the page-title wait rewrite (#662).

`_wait_for_page_title` must delegate to a single `page.wait_for_function`
carrying the expected title and the timeout window, and must keep raising the
original-style message on timeout.
"""

from __future__ import annotations

import pytest
from fakes import PageStub
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from sap_agent.tools.nav import _wait_for_page_title


class RecordingPage(PageStub):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict] = []

    def wait_for_function(self, expression: str, arg=None, timeout=None):
        self.calls.append({"expression": expression, "arg": arg, "timeout": timeout})
        if self.fail:
            raise PlaywrightTimeoutError("Timeout 5000ms exceeded")


def test_waits_with_expected_title_and_timeout() -> None:
    page = RecordingPage()

    _wait_for_page_title(page, "Sales Dashboard", 5_000)

    assert len(page.calls) == 1
    call = page.calls[0]
    assert call["arg"] == "Sales Dashboard"
    assert call["timeout"] == 5_000
    assert "wait_for_function" in call["expression"] or ".sapMTitle" in call["expression"]
    assert "getClientRects" in call["expression"]  # visibility filter preserved


def test_timeout_raises_original_style_message() -> None:
    page = RecordingPage(fail=True)

    with pytest.raises(PlaywrightTimeoutError, match="did not appear within 5000 ms"):
        _wait_for_page_title(page, "Missing Title", 5_000)
