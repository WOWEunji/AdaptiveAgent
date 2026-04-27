"""Adaptive agent orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from adaptive_agent.config import AgentConfig
from adaptive_agent.llms.base import LLMClient
from adaptive_agent.llms.factory import create_llm_client
from adaptive_agent.tools.executor import ToolExecutor
from adaptive_agent.tools.registry import ToolRegistry, create_default_registry


@dataclass
class AgentResponse:
    """Agent 실행 결과."""

    task: str
    output: Any
    tool_name: str | None = None
    action: str = "respond"


class AdaptiveAgent:
    """자연어 작업을 분석하고 필요한 툴을 실행하는 에이전트 골격."""

    def __init__(
        self,
        config: AgentConfig | None = None,
        llm_client: LLMClient | None = None,
        registry: ToolRegistry | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        self.config = config or AgentConfig.from_env()
        self.llm_client = llm_client or create_llm_client(self.config)
        self.registry = registry or create_default_registry(self.config.workspace_dir)
        self.executor = executor or ToolExecutor(self.registry)

    def list_tools(self) -> list:
        """현재 등록된 툴 목록을 반환합니다."""

        return self.registry.list()

    def run(self, task: str) -> AgentResponse:
        """작업을 수행하고 사용자에게 보여줄 응답을 반환합니다."""
        normalized_task = task.strip()
        if not normalized_task:
            return AgentResponse(task=task, output="작업 내용을 입력해 주세요.", action="input_required")

        selected_tool = self.registry.match(normalized_task)
        if selected_tool is not None:
            result = self.executor.run(selected_tool.name, {"task": normalized_task})
            if result.success:
                return AgentResponse(
                    task=normalized_task,
                    output=result.output,
                    tool_name=selected_tool.name,
                    action="tool",
                )
            return AgentResponse(
                task=normalized_task,
                output=f"툴 실행 실패: {result.error}",
                tool_name=selected_tool.name,
                action="tool_error",
            )

        response = self.llm_client.complete(self._build_prompt(normalized_task))
        return AgentResponse(task=normalized_task, output=response, action="llm")

    def _build_prompt(self, task: str) -> str:
        """LLM에 전달할 기본 지시문을 구성합니다."""
        return (
            "You are AdaptiveAgent, a CLI-based assistant that can plan tasks, "
            "suggest tools, and explain next actions in Korean or English.\n"
            f"User task: {task}"
        )
