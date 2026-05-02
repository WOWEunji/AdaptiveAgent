"""Shared contracts for role-specific AdaptiveAgent agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from adaptive_agent.state import AgentState, NodeName


@dataclass(frozen=True)
class AgentResult:
    """Standard result returned by a role agent."""

    next_node: NodeName
    status: str = "ok"
    details: dict[str, object] = field(default_factory=dict)


class AgentRole(Protocol):
    """Minimum interface for a role-specific agent."""

    name: NodeName
    role: str
    prompt_set: str
    prompt_template: str

    def run(self, state: AgentState) -> AgentResult:
        """Read and mutate shared AgentState, then return the next node."""


@dataclass
class BaseRoleAgent:
    """Base role agent with prompt location metadata."""

    name: NodeName
    role: str
    prompt_template: str
    prompt_set: str = "default"

    @property
    def prompt_path(self) -> str:
        """Prompt resource path under `prompts/<prompt_set>/`."""

        return f"{self.prompt_set}/{self.prompt_template}"
