"""Adaptive agent orchestration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from adaptive_agent.config import AgentConfig
from adaptive_agent.llms.base import LLMClient
from adaptive_agent.llms.factory import create_llm_client
from adaptive_agent.state import AgentEvent, AgentState, Message, ToolSchema
from adaptive_agent.tools.executor import ToolExecutor
from adaptive_agent.tools.registry import ToolRegistry, create_default_registry


@dataclass
class AgentResponse:
    """Agent 실행 결과."""

    task: str
    output: Any
    tool_name: str | None = None
    action: str = "respond"
    events: list[AgentEvent] = field(default_factory=list)


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
        state = self._create_state()
        state.record_event("task_received", task=task)

        if task == "":
            state.record_event("clarification_requested", reason="empty_task")
            state.record_event("final_response_created", action="input_required")
            return AgentResponse(
                task=task,
                output="작업 내용을 입력해 주세요.",
                action="input_required",
                events=state.events,
            )

        state.history.append(Message(role="user", content=task))
        plan = self._plan_with_llm(task)
        state.step_count += 1
        state.record_event("task_analyzed", action=plan.get("action", "respond"))
        if plan.get("action") == "tool":
            tool_name = str(plan.get("tool_name") or "")
            arguments = plan.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            state.record_event("tool_execution_requested", tool_name=tool_name)
            result = self.run_tool(tool_name, arguments)
            state.record_event("tool_executed", tool_name=tool_name, success=result.success)
            state.record_event(
                "tool_result_observed",
                tool_name=tool_name,
                success=result.success,
                has_error=result.error is not None,
            )
            if result.success:
                state.record_event("final_response_created", action="tool")
                return AgentResponse(
                    task=task,
                    output=result.output,
                    tool_name=tool_name,
                    action="tool",
                    events=state.events,
                )
            state.failure_count += 1
            state.record_event("failure_classified", reason="tool_execution_error")
            state.record_event("final_response_created", action="tool_error")
            return AgentResponse(
                task=task,
                output=f"툴 실행 실패: {result.error}",
                tool_name=tool_name,
                action="tool_error",
                events=state.events,
            )

        state.record_event("final_response_created", action="llm")
        return AgentResponse(
            task=task,
            output=plan.get("response", ""),
            action="llm",
            events=state.events,
        )

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

    def _create_state(self) -> AgentState:
        """현재 registry를 반영한 실행 상태를 만듭니다."""

        return AgentState(
            available_tools=[
                ToolSchema(
                    name=tool.name,
                    description=tool.description,
                    safety_level=tool.safety_level,
                    source="builtin",
                    validation_status="passed",
                )
                for tool in self.registry.list()
            ]
        )
