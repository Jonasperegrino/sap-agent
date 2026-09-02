"""Extended unit tests for report tool (issue #648)."""

from __future__ import annotations

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from sap_agent.context import SessionContext
from sap_agent.schemas import Config
from sap_agent.tools.report import CLASSIFICATION_MATRIX, collect_artifacts, write_report


class FakeTableLocator:
    def __init__(self) -> None:
        self._called = False

    @property
    def first(self):
        return self

    def wait_for(self, **kw) -> None:
        self._called = True
        raise PlaywrightTimeoutError("no table")  # simulate no table found

    def all(self):
        return []


class FakePage:
    def __init__(self) -> None:
        self.url = "http://localhost:8080/#/dashboard"
        self._screenshot = b"fake-png"

    def screenshot(self) -> bytes:
        return self._screenshot

    def locator(self, selector: str):
        return FakeTableLocator()


class TestClassificationMatrixKeys:
    def test_all_failure_kinds_covered(self) -> None:
        from sap_agent.schemas import AuthFailureKind, FailureKind

        for kind in FailureKind:
            assert kind.value in CLASSIFICATION_MATRIX
        for kind in AuthFailureKind:
            assert kind.value in CLASSIFICATION_MATRIX


class TestWriteReport:
    def test_writes_markdown_file(self, tmp_path) -> None:
        from sap_agent.schemas import BugReport

        config = Config(app_url="http://x", username="u", password="p", artifacts_dir=str(tmp_path))
        ctx = SessionContext(config)
        report = BugReport(title="test bug", expected="works", actual="broken")
        path = write_report(report, ctx)
        assert path.exists()
        content = path.read_text()
        assert "# test bug" in content
        assert "broken" in content

    def test_artifacts_populated(self, tmp_path) -> None:
        from sap_agent.schemas import BugReport

        config = Config(app_url="http://x", username="u", password="p", artifacts_dir=str(tmp_path))
        ctx = SessionContext(config)
        # Create a file in artifacts dir
        (tmp_path / "test.txt").write_text("data")
        report = BugReport(title="t", expected="e", actual="a")
        write_report(report, ctx)
        assert any("test.txt" in a for a in report.artifacts)


class TestCollectArtifacts:
    def test_returns_bug_report(self, tmp_path) -> None:
        config = Config(app_url="http://x", username="u", password="p", artifacts_dir=str(tmp_path))
        ctx = SessionContext(config)
        page = FakePage()
        report = collect_artifacts(page, ctx)
        assert report.title.startswith("http://x")
        assert "agent stuck" in report.title
        assert len(report.environment) > 0
        assert report.environment["app_url"] == "http://x"

    def test_screenshot_saved(self, tmp_path) -> None:
        config = Config(app_url="http://x", username="u", password="p", artifacts_dir=str(tmp_path))
        ctx = SessionContext(config)
        page = FakePage()
        report = collect_artifacts(page, ctx)
        assert len(report.artifacts) >= 1

    def test_trace_tail_included(self, tmp_path) -> None:
        config = Config(app_url="http://x", username="u", password="p", artifacts_dir=str(tmp_path))
        ctx = SessionContext(config)
        ctx.record("auth", "login.start", "navigating")
        page = FakePage()
        report = collect_artifacts(page, ctx)
        assert len(report.trace_tail) >= 1
