"""Unit tests for the answer tool (mock table page, issue #651)."""

from __future__ import annotations

import dataclasses

from fakes import FakeLocator, PageStub

from sap_agent.context import SessionContext
from sap_agent.schemas import Config, IntentConfig, QuestionIntent
from sap_agent.tools.answer import evaluate_question

#: status cells as rendered by UI5 ObjectStatus (label + wrapper noise)
ROWS: list[list[str]] = [
    ["SO-1001", "Acme Corp", "€12,450.00", "Approved\nObject Status\nEntry successfully validated", "2026-01-15"],
    ["SO-1002", "GlobalTech", "€8,230.00", "Pending\nObject Status\nAwaiting approval", "2026-02-03"],
    ["SO-1004", "Nordic Supply", "€21,100.00", "Approved\nObject Status\nEntry successfully validated", "2026-04-01"],
]


@dataclasses.dataclass
class FakePage(PageStub):
    url: str = "http://localhost:8080/#/dashboard"
    rows: list[list[str]] = dataclasses.field(default_factory=lambda: ROWS)

    def locator(self, selector: str):
        return FakeLocator(selector, self.rows)


def _ctx() -> SessionContext:
    return SessionContext(Config(app_url="http://localhost:8080", username="demo", password="x"))


def _count(page: FakePage, status: str, ctx: SessionContext, column: str = "Status"):
    """Count rows via evaluate_question with an explicit COUNT_WHERE intent."""
    result = evaluate_question(
        page,
        f"how many rows have {column} = {status!r}",
        ctx,
        intent=IntentConfig(intent=QuestionIntent.COUNT_WHERE, column=column, value=status, comparer="exact"),
    )
    result.evidence.column = column
    return result


class TestAnswerCountByStatusUnit:
    def test_counts_matching_status_with_evidence(self) -> None:
        result = _count(FakePage(), "Approved", _ctx())
        assert result.answer == 2
        assert result.not_found is False
        assert result.evidence.source == "salesTable"
        assert result.evidence.column == "Status"
        assert result.evidence.matched_rows == 2
        assert result.confidence == "high"

    def test_normalizes_object_status_noise(self) -> None:
        # label "Approved" must not be confused with the wrapper text
        result = _count(FakePage(), "Approved", _ctx())
        assert result.answer == 2
        not_found = _count(FakePage(), "Entry successfully validated", _ctx())
        assert not_found.not_found is True

    def test_unknown_status_returns_not_found_no_crash(self) -> None:
        result = _count(FakePage(), "Purple", _ctx())
        assert result.not_found is True
        assert result.answer is None
        assert result.evidence.matched_rows == 0
        assert "no rows" in result.message

    def test_unknown_column_returns_unsupported(self) -> None:
        result = _count(FakePage(), "x", _ctx(), column="Nope")
        assert result.unsupported is True
        assert result.answer is None
        assert "not present" in result.message

    def test_answer_cross_checked_against_table_rows(self) -> None:
        result = _count(FakePage(), "Approved", _ctx())
        matched = sum(1 for row in ROWS if row[3].split("Object Status", 1)[0].strip() == "Approved")
        assert result.answer == matched == 2

    def test_identical_questions_produce_identical_checksum(self) -> None:
        first = _count(FakePage(), "Approved", _ctx())
        second = _count(FakePage(), "Approved", _ctx())
        third = _count(FakePage(), "Approved", _ctx())
        assert first.model_dump() == second.model_dump() == third.model_dump()
        assert first.checksum == second.checksum == third.checksum
