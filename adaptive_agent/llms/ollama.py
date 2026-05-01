"""Ollama LLM client."""

from __future__ import annotations

from adaptive_agent.llms.base import LLMClient


class OllamaClient:
    """LLM client backed by a local Ollama model."""

    def __init__(self, model: str, *, host: str | None = None) -> None:
        self.model = model
        self.host = host

    def generate(self, prompt: str) -> str:
        import ollama

        client = ollama.Client(host=self.host) if self.host else ollama.Client()
        response = client.chat(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            format="json",
            options={"temperature": 0},
        )
        return str(response["message"]["content"])

    def complete(self, prompt: str) -> str:
        """Compatibility completion method used by the agent core."""

        return self.generate(prompt)


def create_ollama_client(model: str, *, host: str | None = None) -> LLMClient:
    return OllamaClient(model=model, host=host)
