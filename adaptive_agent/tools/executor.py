"""등록된 툴을 표준화된 방식으로 실행합니다."""

from __future__ import annotations

from typing import Any

from adaptive_agent.tools.models import ToolExecutionResult
from adaptive_agent.tools.registry import ToolRegistry


class ToolExecutor:
    """ToolRegistry에 등록된 툴을 실행합니다."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def run(self, tool_name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        """툴 이름과 인자를 받아 실행 결과를 반환합니다."""

        tool = self.registry.get(tool_name)
        if tool is None:
            return ToolExecutionResult(success=False, output="", error=f"Unknown tool: {tool_name}")

        try:
            return tool.handler(arguments)
        except Exception as exc:  # pragma: no cover - 안전망
            return ToolExecutionResult(success=False, output="", error=str(exc))
