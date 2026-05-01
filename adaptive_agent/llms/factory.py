"""LLM client factory."""

from __future__ import annotations

from adaptive_agent.config import AgentConfig
from adaptive_agent.llms.base import LLMClient
from adaptive_agent.llms.ollama import OllamaClient


def create_llm_client(config: AgentConfig, provider: str | None = None) -> LLMClient:
    """Create an LLM client for the selected provider."""
    selected_provider = (provider or config.llm_provider).lower()
    if selected_provider == "ollama":
        return OllamaClient(model=config.ollama_model, host=config.ollama_host)
    if selected_provider == "openai":
        from adaptive_agent.llms.openai_client import OpenAIClient

        return OpenAIClient(model=config.openai_model)
    if selected_provider in ("gemini", "google"):
        from adaptive_agent.llms.gemini_client import GeminiClient

        return GeminiClient(model=config.gemini_model)

    raise ValueError(f"Unsupported LLM provider: {selected_provider}")
