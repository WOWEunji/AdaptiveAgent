"""Standard execution boundary for registered tools."""

from __future__ import annotations

from typing import Any

from adaptive_agent.tools.models import ToolExecutionResult
from adaptive_agent.tools.registry import ToolRegistry


class ToolExecutor:
    """Executor for tools stored in ToolRegistry."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def run(self, tool_name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        """Run a registered tool by name with structured arguments."""

        tool = self.registry.get(tool_name)
        if tool is None:
            return ToolExecutionResult(success=False, output="", error=f"Unknown tool: {tool_name}")

        try:
            return tool.handler(arguments)
        except Exception as exc:  # pragma: no cover - execution boundary guard
            return ToolExecutionResult(success=False, output="", error=str(exc))
