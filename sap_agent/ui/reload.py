"""Drop stale ``sap_agent`` modules after a deploy.

Streamlit Cloud pulls new code into a running process. The page script is
re-executed on every rerun, but modules it imported earlier stay cached in
``sys.modules`` — so a deploy that changes both the page and a module it uses
leaves the two out of step until someone reboots the app. This guard notices
that the package sources changed and forgets the cached modules, so the next
import reads the new code.
"""

from __future__ import annotations

import sys
import types
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

#: survives the purge because it is not part of the ``sap_agent`` package
_STATE_MODULE = "_atlas_reload_state"


def source_signature(package_dir: Path) -> int:
    """Newest modification time across the package's Python sources (0 if unreadable)."""
    newest = 0
    try:
        for path in package_dir.rglob("*.py"):
            newest = max(newest, path.stat().st_mtime_ns)
    except OSError:
        return 0
    return newest


def purge_stale_modules(package_dir: Path, package: str = "sap_agent") -> bool:
    """Forget cached ``package`` modules when its sources changed since the last call.

    Returns True when modules were purged. The first call only records the
    signature. An unreadable source tree never purges.
    """
    signature = source_signature(package_dir)
    if not signature:
        return False
    state = sys.modules.setdefault(_STATE_MODULE, types.ModuleType(_STATE_MODULE))
    previous = vars(state).get("signature")
    vars(state)["signature"] = signature
    if previous is None or previous == signature:
        return False
    for name in [m for m in sys.modules if m == package or m.startswith(package + ".")]:
        sys.modules.pop(name, None)
    return True
