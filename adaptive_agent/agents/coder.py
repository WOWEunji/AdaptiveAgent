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
        """Create code metadata and route back to execution.

        Validates that the merged ``tool_create`` arguments carry non-empty
        ``name``/``description``/``code`` strings. If any required field is
        missing, the next node is set to ``error`` with a ``coder_arguments_invalid``
        event so the router can surface a structured failure instead of letting
        ``tool_create`` raise a generic argument error downstream.
        """

        state.record_event("agent_started", agent_role=self.role, node=self.name)
        generated = self.coder(state)
        code = generated.get("code") if isinstance(generated, dict) else None
        if isinstance(code, str):
            state.generated_code = code
        arguments = dict(state.current_plan.get("arguments", {}))
        if isinstance(generated, dict):
            for key in ("code", "description", "parameters", "returns"):
                if key in generated and key not in arguments:
                    arguments[key] = generated[key]

        missing = _missing_required_fields(arguments)
        if missing:
            state.next_node = "error"
            state.error_log = (
                "tool_create 인자가 부족합니다: "
                + ", ".join(missing)
            )
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

        state.current_plan = {"action": "tool", "tool_name": "tool_create", "arguments": arguments}
        state.next_node = "execute"
        state.record_event("tool_code_created", agent_role=self.role, tool_name=arguments.get("name"), code=code)
        state.record_event("agent_finished", agent_role=self.role, node=self.name, next_node=state.next_node)
        return AgentResult(next_node=state.next_node, details={"generated": generated})


_REQUIRED_TOOL_CREATE_FIELDS = ("name", "description", "code")


def _missing_required_fields(arguments: dict[str, Any]) -> list[str]:
    """Return required ``tool_create`` fields that are absent or blank."""

    missing: list[str] = []
    for key in _REQUIRED_TOOL_CREATE_FIELDS:
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            missing.append(key)
    return missing


def _default_coder(_state: AgentState) -> dict[str, Any]:
    return {}
