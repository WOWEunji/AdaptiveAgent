"""Executor role agent implementation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from adaptive_agent.agents.base import AgentResult, BaseRoleAgent
from adaptive_agent.state import AgentState


class ExecutorAgent(BaseRoleAgent):
    """Agent that executes the current normalized plan."""

    executor: Callable[[str, dict[str, Any], AgentState], Any]

    def __init__(self, executor: Callable[[str, dict[str, Any], AgentState], Any]) -> None:
        super().__init__(name="execute", role="executor", prompt_template="")
        self.executor = executor

    def run(self, state: AgentState) -> AgentResult:
        """Execute `AgentState.current_plan` and return the next node."""

        state.record_event("agent_started", agent_role=self.role, node=self.name)
        response = self.executor(state.user_task, state.current_plan, state)
        details: dict[str, object] = {}
        if response is not None:
            details["response"] = response
        state.record_event("agent_finished", agent_role=self.role, node=self.name, next_node=state.next_node)
        return AgentResult(next_node=state.next_node, details=details)
