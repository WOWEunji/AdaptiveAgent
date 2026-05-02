"""Librarian role agent implementation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from adaptive_agent.agents.base import AgentResult, BaseRoleAgent
from adaptive_agent.state import AgentState


class LibrarianAgent(BaseRoleAgent):
    """Agent that retrieves approved tools and records catalog events."""

    retriever: Callable[[AgentState], list[dict[str, Any]]]

    def __init__(self, retriever: Callable[[AgentState], list[dict[str, Any]]] | None = None) -> None:
        super().__init__(name="retrieve", role="librarian", prompt_template="")
        self.retriever = retriever or _default_retriever

    def run(self, state: AgentState) -> AgentResult:
        """Retrieve skills for planning context."""

        state.retrieved_skills = self.retriever(state)
        state.record_event(
            "skills_retrieved",
            agent_role=self.role,
            count=len(state.retrieved_skills),
        )
        state.next_node = "plan"
        return AgentResult(next_node=state.next_node, details={"skills": state.retrieved_skills})


def _default_retriever(_state: AgentState) -> list[dict[str, Any]]:
    return []
