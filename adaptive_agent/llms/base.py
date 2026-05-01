"""LLM provider interfaces."""

from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    """Minimum protocol implemented by LLM providers."""

    def generate(self, prompt: str) -> str:
        """Generate text from a prompt."""

    def complete(self, prompt: str) -> str:
        """Compatibility completion method used by the agent core."""
