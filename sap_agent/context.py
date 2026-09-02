"""Session context shared across tools: goal, trace, artifact paths.

Rules: secrets never enter `trace`; artifacts are written under the session
artifact dir so bug reports can reference them without leaking credentials.
"""

from __future__ import annotations

from pathlib import Path

from .schemas import Config, TraceEntry


class SessionContext:
    """Mutable per-run state passed through the agent loop."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.trace: list[TraceEntry] = []
        self.artifacts_dir = Path(config.artifacts_dir)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def record(self, tool: str, action: str, outcome: str, url: str | None = None, detail: str = "") -> None:
        self.trace.append(TraceEntry(tool=tool, action=action, outcome=outcome, url=url, detail=detail))

    def artifact_path(self, name: str) -> Path:
        return self.artifacts_dir / name

    def trace_lines(self) -> list[str]:
        return [entry.model_dump_json() for entry in self.trace]

    def snapshot(self) -> list[dict]:
        """Serializable trace copy (JSON-friendly, no secrets by contract)."""
        return [entry.model_dump() for entry in self.trace]
