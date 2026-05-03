"""Plan role agent implementation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from adaptive_agent.agents.base import AgentResult, BaseRoleAgent
from adaptive_agent.state import AgentState


class PlanAgent(BaseRoleAgent):
    """Agent that creates an executable plan for the current state."""

    planner: Callable[[AgentState], dict[str, Any]]

    def __init__(self, planner: Callable[[AgentState], dict[str, Any]]) -> None:
        super().__init__(name="plan", role="planner", prompt_template="plan.txt")
        self.planner = planner

    def run(self, state: AgentState) -> AgentResult:
        """Store the generated plan and choose the next router node."""

        plan = self.planner(state)
        state.step_count += 1
        state.current_plan = dict(plan)

        validation_error = plan.pop("_validation_error", None)
        if validation_error:
            state.record_event("plan_validation_failed", reason=validation_error)
        state.record_event("task_analyzed", action=plan.get("action", "respond"), agent_role=self.role)

        arguments = plan.get("arguments") or {}
        needs_coding = (
            plan.get("action") == "tool"
            and plan.get("tool_name") in {"code_execute", "tool_create"}
            and isinstance(arguments, dict)
            and not isinstance(arguments.get("code"), str)
        )
        if needs_coding:
            next_node = "code"
        elif plan.get("action") in {"tool", "parallel"}:
            next_node = "execute"
        elif plan.get("needs_user_input"):
            next_node = "approve"
        else:
            next_node = "done"
        state.next_node = next_node
        return AgentResult(next_node=next_node, details={"plan": plan})
