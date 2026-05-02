"""Coder role agent contract."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from adaptive_agent.agents.base import AgentResult, BaseRoleAgent
from adaptive_agent.state import AgentState


class CoderAgent(BaseRoleAgent):
    """Agent that turns a create-tool intent into reusable tool code."""

    coder: Callable[[AgentState], dict[str, Any]]

    def __init__(self, coder: Callable[[AgentState], dict[str, Any]] | None = None) -> None:
        super().__init__(name="code", role="coder", prompt_template="coder.txt")
        self.coder = coder or _default_coder

    def run(self, state: AgentState) -> AgentResult:
        """Create code metadata and route back to execution."""

        state.record_event("agent_started", agent_role=self.role, node=self.name)
        generated = self.coder(state)
        code = generated.get("code")
        if isinstance(code, str):
            state.generated_code = code
        arguments = dict(state.current_plan.get("arguments", {}))
        for key in ("code", "description", "parameters", "returns"):
            if key in generated and key not in arguments:
                arguments[key] = generated[key]
        state.current_plan = {"action": "tool", "tool_name": "tool_create", "arguments": arguments}
        state.next_node = "execute"
        state.record_event("tool_code_created", agent_role=self.role, tool_name=arguments.get("name"), code=code)
        state.record_event("agent_finished", agent_role=self.role, node=self.name, next_node=state.next_node)
        return AgentResult(next_node=state.next_node, details={"generated": generated})


def _default_coder(_state: AgentState) -> dict[str, Any]:
    return {}
