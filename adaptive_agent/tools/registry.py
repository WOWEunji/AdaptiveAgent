"""Tool registry and built-in keyword-matched tools."""

from __future__ import annotations

from adaptive_agent.tools.models import Tool, ToolExecutionResult


class ToolRegistry:
    """실행 가능한 툴을 등록하고 간단한 키워드로 매칭합니다."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def match(self, task: str) -> Tool | None:
        normalized = task.lower()
        for tool in self._tools.values():
            if any(keyword.lower() in normalized for keyword in tool.keywords):
                return tool
        return None

    def list(self) -> list[Tool]:
        return list(self._tools.values())


def create_default_registry() -> ToolRegistry:
    """초기 프로젝트에서 사용할 내장 툴을 등록합니다."""

    registry = ToolRegistry()

    def echo(arguments: dict[str, object]) -> ToolExecutionResult:
        return ToolExecutionResult(success=True, output=arguments.get("task", ""))

    registry.register(
        Tool(
            name="echo",
            description="입력 작업을 그대로 반환하는 상태 확인용 툴입니다.",
            keywords=("echo", "반복", "그대로", "ping"),
            handler=echo,
        )
    )
    return registry
