"""LLM provider interfaces."""

from __future__ import annotations

from typing import Protocol

from adaptive_agent.llms.usage import LLMUsage


class LLMClient(Protocol):
    """Minimum protocol implemented by LLM providers.

    Implementations should set ``self.last_usage = LLMUsage(...)`` immediately
    after each ``complete()`` call so the agent can aggregate token counts
    and cost estimates without changing the str-returning contract. Stub
    or test clients may leave ``last_usage`` as ``None``.
    """

    last_usage: LLMUsage | None

    def generate(self, prompt: str) -> str:
        """Generate text from a prompt."""

    def complete(self, prompt: str) -> str:
        """Compatibility completion method used by the agent core."""
