"""Critic agent node contract."""

from __future__ import annotations

from adaptive_agent.nodes.base import BaseAgentNode


class CriticNode(BaseAgentNode):
    """Node metadata for future critique and reflection workflows."""

    def __init__(self) -> None:
        super().__init__(name="critique", prompt_template="critic.txt")
