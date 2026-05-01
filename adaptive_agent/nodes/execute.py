"""Tool execution node for normalized plans."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from adaptive_agent.nodes.base import BaseAgentNode, NodeResult
from adaptive_agent.state import AgentState


class ExecuteNode(BaseAgentNode):
    """Node that executes the current normalized tool plan."""

    executor: Callable[[str, dict[str, Any], AgentState], Any]

    def __init__(self, executor: Callable[[str, dict[str, Any], AgentState], Any]) -> None:
        super().__init__(name="execute", prompt_template="")
        self.executor = executor

    def run(self, state: AgentState) -> NodeResult:
        """Execute `AgentState.current_plan` and return the next node."""

        response = self.executor(state.user_task, state.current_plan, state)
        details: dict[str, object] = {}
        if response is not None:
            details["response"] = response
        return NodeResult(next_node=state.next_node, details=details)
