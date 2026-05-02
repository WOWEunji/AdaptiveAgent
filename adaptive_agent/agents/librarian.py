"""Librarian role agent implementation."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from adaptive_agent.agents.base import AgentResult, BaseRoleAgent
from adaptive_agent.state import AgentState

if TYPE_CHECKING:
    from adaptive_agent.skills import SkillCatalog


class LibrarianAgent(BaseRoleAgent):
    """Agent that retrieves approved tools and records catalog events.

    When a :class:`SkillCatalog` is wired in, the librarian also:

    - audits manifest integrity on every ``run()`` and surfaces ``stale_count``
      in the ``skills_retrieved`` event details so planners can avoid
      recommending broken entries;
    - exposes :meth:`record_usage` so callers (typically the executor) can
      increment ``usage_count``/``failure_count`` after each generated-tool run.

    Without a catalog reference the librarian remains a pure retrieval shim,
    which keeps existing routing/test wiring unchanged.
    """

    retriever: Callable[[AgentState], list[dict[str, Any]]]

    def __init__(
        self,
        retriever: Callable[[AgentState], list[dict[str, Any]]] | None = None,
        *,
        catalog: "SkillCatalog | None" = None,
    ) -> None:
        super().__init__(name="retrieve", role="librarian", prompt_template="")
        self.retriever = retriever or _default_retriever
        self.catalog = catalog

    def run(self, state: AgentState) -> AgentResult:
        """Retrieve skills for planning context and (optionally) audit catalog."""

        state.retrieved_skills = self.retriever(state)
        event_details: dict[str, Any] = {
            "agent_role": self.role,
            "count": len(state.retrieved_skills),
        }
        if self.catalog is not None:
            stale_entries = self.catalog.find_stale_entries()
            event_details["stale_count"] = len(stale_entries)
            if stale_entries:
                state.record_event(
                    "catalog_audit_stale_entries",
                    agent_role=self.role,
                    stale=stale_entries,
                )
        state.record_event("skills_retrieved", **event_details)
        state.next_node = "plan"
        return AgentResult(next_node=state.next_node, details={"skills": state.retrieved_skills})

    def record_usage(self, name: str, *, success: bool) -> dict[str, Any] | None:
        """Increment usage stats for ``name`` if a catalog is wired in.

        Returns the updated metadata, or ``None`` when the librarian has no
        catalog or the tool isn't in the manifest (builtins, ad-hoc tools).
        """

        if self.catalog is None:
            return None
        return self.catalog.record_usage(name, success=success)


def _default_retriever(_state: AgentState) -> list[dict[str, Any]]:
    return []
