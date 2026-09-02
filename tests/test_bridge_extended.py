"""Extended unit tests for bridge fill_login_form."""

from __future__ import annotations

from sap_agent.ui5.bridge import fill_login_form


class FakeLocator:
    def __init__(self) -> None:
        self._filled: list[str] = []
        self._clicked = False

    def wait_for(self, state: str = "visible", timeout: int = 10_000) -> None:
        pass

    def fill(self, value: str) -> None:
        self._filled.append(value)

    def click(self) -> None:
        self._clicked = True


class FakePage:
    def __init__(self) -> None:
        self.user_loc = FakeLocator()
        self.pwd_loc = FakeLocator()
        self.btn_loc = FakeLocator()

    def locator(self, selector: str):
        if "Username" in selector:
            return self.user_loc
        if "Password" in selector:
            return self.pwd_loc
        if "Log In" in selector:
            return self.btn_loc
        return FakeLocator()


class TestFillLoginForm:
    def test_fills_and_clicks(self) -> None:
        page = FakePage()
        fill_login_form(page, "admin", "secret123")
        assert page.user_loc._filled == ["admin"]
        assert page.pwd_loc._filled == ["secret123"]
        assert page.btn_loc._clicked is True
