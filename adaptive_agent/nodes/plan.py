"""Plan agent node implementation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from adaptive_agent.nodes.base import BaseAgentNode, NodeResult
from adaptive_agent.state import AgentState


class PlanNode(BaseAgentNode):
    """Plan node that creates the first executable task plan."""

    planner: Callable[[str], dict[str, Any]]

    def __init__(self, planner: Callable[[str], dict[str, Any]]) -> None:
        super().__init__(name="plan", prompt_template="plan.txt")
        self.planner = planner

    def run(self, state: AgentState) -> NodeResult:
        """Store the generated plan and choose the next router node."""

        plan = self.planner(state.user_task)
        state.step_count += 1
        state.current_plan = dict(plan)

        validation_error = plan.pop("_validation_error", None)
        if validation_error:
            state.record_event("plan_validation_failed", reason=validation_error)
        state.record_event("task_analyzed", action=plan.get("action", "respond"))

        if plan.get("action") == "tool":
            next_node = "execute"
        elif plan.get("needs_user_input"):
            next_node = "approve"
        else:
            next_node = "done"
        state.next_node = next_node
        return NodeResult(next_node=next_node, details={"plan": plan})
