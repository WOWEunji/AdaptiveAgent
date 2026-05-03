"""Coder role agent contract."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from adaptive_agent.agents.base import AgentResult, BaseRoleAgent
from adaptive_agent.state import AgentState

_TOOL_CREATE_REQUIRED = ("name", "description", "code")
_CODE_EXECUTE_REQUIRED = ("code",)


class CoderAgent(BaseRoleAgent):
    """Agent that writes code for a plan — either a one-shot script or a reusable tool."""

    coder: Callable[[AgentState], dict[str, Any]]

    def __init__(self, coder: Callable[[AgentState], dict[str, Any]] | None = None) -> None:
        super().__init__(name="code", role="coder", prompt_template="coder.txt")
        self.coder = coder or _default_coder

    def run(self, state: AgentState) -> AgentResult:
        """Write code for the current plan and route to execution.

        Handles two modes based on state.current_plan["tool_name"]:
        - "code_execute": generates an inline script; only "code" is required.
        - "tool_create": generates a reusable run() function; "name", "description",
          and "code" are all required.
        """

        state.record_event("agent_started", agent_role=self.role, node=self.name)

        tool_name = state.current_plan.get("tool_name", "tool_create")
        is_code_execute = tool_name == "code_execute"

        generated = self.coder(state)
        code = generated.get("code") if isinstance(generated, dict) else None
        if isinstance(code, str):
            state.generated_code = code

        arguments = dict(state.current_plan.get("arguments") or {})
        if isinstance(generated, dict):
            for key in ("code", "description", "parameters", "returns"):
                if key in generated and key not in arguments:
                    arguments[key] = generated[key]

        required_fields = _CODE_EXECUTE_REQUIRED if is_code_execute else _TOOL_CREATE_REQUIRED
        missing = _missing_fields(arguments, required_fields)
        if missing:
            state.next_node = "error"
            state.error_log = f"{tool_name} 인자가 부족합니다: " + ", ".join(missing)
            state.record_event(
                "coder_arguments_invalid",
                agent_role=self.role,
                missing_fields=missing,
            )
            state.record_event("agent_finished", agent_role=self.role, node=self.name, next_node=state.next_node)
            return AgentResult(
                next_node=state.next_node,
                status="invalid_arguments",
                details={"generated": generated, "missing_fields": missing},
            )

        state.current_plan = {"action": "tool", "tool_name": tool_name, "arguments": arguments}
        state.next_node = "execute"

        event_details: dict[str, Any] = {"agent_role": self.role, "code": code}
        if not is_code_execute:
            event_details["tool_name"] = arguments.get("name")
        state.record_event("tool_code_created", **event_details)
        state.record_event("agent_finished", agent_role=self.role, node=self.name, next_node=state.next_node)
        return AgentResult(next_node=state.next_node, details={"generated": generated})


def _missing_fields(arguments: dict[str, Any], required: tuple[str, ...]) -> list[str]:
    """Return required fields that are absent or blank."""

    return [k for k in required if not isinstance(arguments.get(k), str) or not arguments[k].strip()]


def _default_coder(_state: AgentState) -> dict[str, Any]:
    return {}
