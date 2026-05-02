"""LLM provider interfaces."""

from __future__ import annotations

from typing import Iterator, Protocol


class LLMClient(Protocol):
    """Minimum protocol implemented by LLM providers.

    ``stream`` is opt-in; the default implementation in concrete clients
    yields the full ``complete`` result in a single chunk so callers that
    consume the iterator always work, even with providers that do not
    natively stream. Provider-specific streaming (e.g. Ollama's chat
    ``stream=True``) overrides this for true incremental output.
    """

    def generate(self, prompt: str) -> str:
        """Generate text from a prompt."""

    def complete(self, prompt: str) -> str:
        """Compatibility completion method used by the agent core."""

    def stream(self, prompt: str) -> Iterator[str]:
        """Yield response chunks for a prompt; default = single chunk."""
