"""Coder agent node contract."""

from __future__ import annotations

from adaptive_agent.nodes.base import BaseAgentNode


class CoderNode(BaseAgentNode):
    """Node metadata for future generated-code workflows."""

    def __init__(self) -> None:
        super().__init__(name="code", prompt_template="coder.txt")
