"""Ollama LLM client."""

from __future__ import annotations

from adaptive_agent.llms.base import LLMClient


class OllamaClient:
    """로컬 Ollama 모델을 사용하는 LLM 클라이언트."""

    def __init__(self, model: str) -> None:
        self.model = model

    def generate(self, prompt: str) -> str:
        import ollama

        response = ollama.chat(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        return str(response["message"]["content"])

    def complete(self, prompt: str) -> str:
        """Agent core가 사용하는 표준 completion 인터페이스입니다."""

        return self.generate(prompt)


def create_ollama_client(model: str) -> LLMClient:
    return OllamaClient(model=model)
