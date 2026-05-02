"""Critic role agent implementation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from adaptive_agent.agents.base import AgentResult, BaseRoleAgent
from adaptive_agent.state import AgentState, NodeName


class CriticAgent(BaseRoleAgent):
    """Agent that evaluates the latest execution observation."""

    critic: Callable[[AgentState], dict[str, Any]]

    def __init__(self, critic: Callable[[AgentState], dict[str, Any]] | None = None) -> None:
        super().__init__(name="critique", role="critic", prompt_template="critic.txt")
        self.critic = critic or _default_critic

    def run(self, state: AgentState) -> AgentResult:
        """Classify execution outcome and choose the next node."""

        verdict = self.critic(state)
        normalized_verdict = str(verdict.get("verdict") or "success").strip().lower().replace("-", "_")
        reflection = verdict.get("reflection")
        if isinstance(reflection, str) and reflection:
            state.reflections.append(reflection)

        next_node = _next_node_for_verdict(normalized_verdict, verdict.get("next_node"))
        state.next_node = next_node
        state.record_event(
            "execution_critiqued",
            agent_role=self.role,
            verdict=normalized_verdict,
            next_node=next_node,
            has_reflection=bool(reflection),
        )
        return AgentResult(next_node=next_node, details={"critique": verdict})


def _default_critic(state: AgentState) -> dict[str, Any]:
    result = state.last_tool_result or {}
    return {
        "verdict": "success" if result.get("success") is not False else "failed",
        "reason": "default critic",
        "reflection": "",
        "next_node": "done",
    }


def _next_node_for_verdict(verdict: str, requested_next_node: object) -> NodeName:
    if requested_next_node in {"plan", "approve", "store", "done", "error"}:
        return requested_next_node  # type: ignore[return-value]
    if verdict in {"success", "accepted", "pass", "passed"}:
        return "done"
    if verdict in {"retry", "retryable", "retry_needed"}:
        return "plan"
    if verdict in {"needs_human", "needs_input", "approval_required"}:
        return "approve"
    return "error"
