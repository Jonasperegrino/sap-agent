"""SAP Fiori discovery agent."""

__version__ = "0.1.0"

__all__ = [
    "Config",
    "SessionContext",
    "__version__",
]

from .context import SessionContext
from .schemas import Config
