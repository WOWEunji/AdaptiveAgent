"""Ollama LLM client."""

from __future__ import annotations

from typing import Iterator

from adaptive_agent.llms.base import LLMClient


class OllamaClient:
    """LLM client backed by a local Ollama model."""

    def __init__(
        self,
        model: str,
        *,
        host: str | None = None,
        timeout_seconds: float = 60.0,
        num_predict: int = 256,
        think: bool = False,
    ) -> None:
        self.model = model
        self.host = host
        self.timeout_seconds = timeout_seconds
        self.num_predict = num_predict
        self.think = think

    def generate(self, prompt: str) -> str:
        import ollama

        client_kwargs = {"timeout": self.timeout_seconds}
        if self.host:
            client_kwargs["host"] = self.host
        client = ollama.Client(**client_kwargs)
        response = client.chat(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            format="json",
            options={"temperature": 0, "num_predict": self.num_predict},
            think=self.think,
        )
        return str(response["message"]["content"])

    def complete(self, prompt: str) -> str:
        """Compatibility completion method used by the agent core."""

        return self.generate(prompt)

    def stream(self, prompt: str) -> Iterator[str]:
        """Stream chunks from Ollama natively (chat with stream=True)."""

        import ollama

        client_kwargs = {"timeout": self.timeout_seconds}
        if self.host:
            client_kwargs["host"] = self.host
        client = ollama.Client(**client_kwargs)
        stream = client.chat(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0, "num_predict": self.num_predict},
            stream=True,
        )
        for chunk in stream:
            piece = chunk.get("message", {}).get("content", "")
            if piece:
                yield str(piece)


def create_ollama_client(
    model: str,
    *,
    host: str | None = None,
    timeout_seconds: float = 60.0,
    num_predict: int = 256,
    think: bool = False,
) -> LLMClient:
    return OllamaClient(
        model=model,
        host=host,
        timeout_seconds=timeout_seconds,
        num_predict=num_predict,
        think=think,
    )
