"""Shared contracts for AdaptiveAgent role nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from adaptive_agent.state import AgentState, NodeName


@dataclass(frozen=True)
class NodeResult:
    """Standard node result used by the router for state transitions."""

    next_node: NodeName
    status: str = "ok"
    details: dict[str, object] = field(default_factory=dict)


class AgentNode(Protocol):
    """Minimum interface for role-specific agent nodes."""

    name: NodeName
    prompt_set: str
    prompt_template: str

    def run(self, state: AgentState) -> NodeResult:
        """Read and mutate shared AgentState, then return the next node."""


@dataclass
class BaseAgentNode:
    """Base node with prompt location metadata."""

    name: NodeName
    prompt_template: str
    prompt_set: str = "default"

    @property
    def prompt_path(self) -> str:
        """Prompt resource path under `prompts/<prompt_set>/`."""

        return f"{self.prompt_set}/{self.prompt_template}"
