"""LLM client factory."""

from __future__ import annotations

from adaptive_agent.config import AgentConfig
from adaptive_agent.llms.base import LLMClient
from adaptive_agent.llms.ollama import OllamaClient


def create_llm_client(config: AgentConfig, provider: str | None = None) -> LLMClient:
    """설정에 맞는 LLM 클라이언트를 반환합니다."""
    selected_provider = (provider or config.llm_provider).lower()
    if selected_provider == "ollama":
        return OllamaClient(model=config.ollama_model)

    raise ValueError(f"Unsupported LLM provider: {selected_provider}")
