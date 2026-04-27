"""OpenAI Chat Completions API client."""

from __future__ import annotations

import os

from adaptive_agent.llms.base import LLMClient


class OpenAIClient:
    """OpenAI `chat.completions` 기반 최소 클라이언트."""

    def __init__(self, model: str, *, api_key: str | None = None) -> None:
        self._model = model
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self._api_key:
            msg = "OPENAI_API_KEY가 설정되어 있지 않습니다."
            raise ValueError(msg)

    def generate(self, prompt: str) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=self._api_key)
        response = client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
        )
        choice = response.choices[0].message.content
        return choice if choice is not None else ""


def create_openai_client(model: str, *, api_key: str | None = None) -> LLMClient:
    return OpenAIClient(model=model, api_key=api_key)
