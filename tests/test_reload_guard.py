"""Stale-module guard: a deploy that changes package sources drops the cached modules."""

from __future__ import annotations

import os
import sys
import types
from typing import TYPE_CHECKING

from sap_agent.ui import reload as guard

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _package(tmp_path: Path) -> Path:
    pkg = tmp_path / "fakepkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("VALUE = 1\n")
    (pkg / "mod.py").write_text("X = 1\n")
    return pkg


def test_first_call_records_and_unchanged_sources_keep_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, guard._STATE_MODULE, raising=False)
    monkeypatch.setitem(sys.modules, "fakepkg", types.ModuleType("fakepkg"))
    pkg = _package(tmp_path)
    assert guard.purge_stale_modules(pkg, "fakepkg") is False
    assert guard.purge_stale_modules(pkg, "fakepkg") is False
    assert "fakepkg" in sys.modules


def test_changed_source_purges_the_package_but_nothing_else(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, guard._STATE_MODULE, raising=False)
    for name in ("fakepkg", "fakepkg.mod", "fakepkg_other"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    pkg = _package(tmp_path)
    guard.purge_stale_modules(pkg, "fakepkg")
    newer = (pkg / "mod.py").stat().st_mtime_ns + 5_000_000_000
    os.utime(pkg / "mod.py", ns=(newer, newer))
    assert guard.purge_stale_modules(pkg, "fakepkg") is True
    assert "fakepkg" not in sys.modules and "fakepkg.mod" not in sys.modules
    assert "fakepkg_other" in sys.modules
    # Settled again: the next rerun leaves freshly imported modules alone.
    monkeypatch.setitem(sys.modules, "fakepkg", types.ModuleType("fakepkg"))
    assert guard.purge_stale_modules(pkg, "fakepkg") is False
    assert "fakepkg" in sys.modules


def test_unreadable_tree_never_purges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, guard._STATE_MODULE, raising=False)
    monkeypatch.setitem(sys.modules, "fakepkg", types.ModuleType("fakepkg"))
    assert guard.purge_stale_modules(tmp_path / "missing", "fakepkg") is False
    assert "fakepkg" in sys.modules
