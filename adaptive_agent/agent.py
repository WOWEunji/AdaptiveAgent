"""Adaptive agent orchestration."""

from __future__ import annotations

import json
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
        """사용자 원문 task를 LLM 계획에 따라 수행합니다."""
        if task == "":
            return AgentResponse(task=task, output="작업 내용을 입력해 주세요.", action="input_required")

        plan = self._plan_with_llm(task)
        if plan.get("action") == "tool":
            tool_name = str(plan.get("tool_name") or "")
            arguments = plan.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            result = self.run_tool(tool_name, arguments)
            if result.success:
                return AgentResponse(
                    task=task,
                    output=result.output,
                    tool_name=tool_name,
                    action="tool",
                )
            return AgentResponse(
                task=task,
                output=f"툴 실행 실패: {result.error}",
                tool_name=tool_name,
                action="tool_error",
            )

        return AgentResponse(task=task, output=plan.get("response", ""), action="llm")

    def run_tool(self, tool_name: str, arguments: dict[str, Any]):
        """명시적으로 지정된 툴을 실행합니다. 자연어 매칭을 수행하지 않습니다."""

        return self.executor.run(tool_name, arguments)

    def _plan_with_llm(self, task: str) -> dict[str, Any]:
        """LLM에게 원문 task와 툴 목록을 전달해 실행 계획을 받습니다."""

        response = self.llm_client.complete(self._build_prompt(task))
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError:
            return {"action": "respond", "response": response}

        if not isinstance(parsed, dict):
            return {"action": "respond", "response": response}
        return parsed

    def _build_prompt(self, task: str) -> str:
        """LLM에 전달할 기본 지시문을 구성합니다."""
        tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "category": tool.category,
                "usage": tool.usage,
            }
            for tool in self.registry.list()
        ]
        return (
            "You are AdaptiveAgent. Keep the user's task exactly as provided: do not rewrite, "
            "trim, translate, change casing, or transform it. Decide using the original task only.\n"
            "Return only JSON in one of these forms:\n"
            '{"action":"tool","tool_name":"<tool name>","arguments":{...}}\n'
            '{"action":"respond","response":"<answer>"}\n'
            f"Available tools: {json.dumps(tools, ensure_ascii=False)}\n"
            f"Original user task: {task}"
        )
