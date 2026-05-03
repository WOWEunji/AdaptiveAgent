"""LLM client factory."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable

from adaptive_agent.config import AgentConfig
from adaptive_agent.llms.base import LLMClient
from adaptive_agent.llms.ollama import OllamaClient


def create_llm_client(config: AgentConfig, provider: str | None = None) -> LLMClient:
    """Create an LLM client for the selected provider."""
    selected_provider = (provider or config.llm_provider).lower()
    if selected_provider == "ollama":
        return OllamaClient(
            model=config.ollama_model,
            host=config.ollama_host,
            port=config.ollama_port,
            timeout_seconds=config.ollama_timeout_seconds,
            num_predict=config.ollama_num_predict,
            think=config.ollama_think,
        )
    if selected_provider == "openai":
        from adaptive_agent.llms.openai_client import OpenAIClient

        return OpenAIClient(model=config.openai_model)
    if selected_provider == "openrouter":
        from adaptive_agent.llms.openrouter_client import OpenRouterClient

        return OpenRouterClient(model=config.openrouter_model, api_key=config.openrouter_api_key or None)
    raise ValueError(f"Unsupported LLM provider: {selected_provider}")


def create_coder_llm_client(config: AgentConfig) -> "LLMClient | None":
    """Create a dedicated LLM client for the Coder Agent, or None if not configured.

    Returns None when ``config.coder_provider`` is empty, which signals the
    caller to fall back to the default ``llm_client``.
    """
    if not config.coder_provider:
        return None
    from dataclasses import replace as _replace
    provider = config.coder_provider
    model = config.coder_model
    patched = _replace(
        config,
        openai_model=model if provider == "openai" and model else config.openai_model,
        ollama_model=model if provider == "ollama" and model else config.ollama_model,
        openrouter_model=model if provider == "openrouter" and model else config.openrouter_model,
    )
    return create_llm_client(patched, provider=provider)


def create_embedding_fn(config: AgentConfig) -> Callable[[str], list[float]] | None:
    """Return an embedding function for the configured provider, or None.

    Currently supports ``openai`` provider using ``text-embedding-3-small``
    (or ``config.openai_embedding_model``). Returns ``None`` when the provider
    is not OpenAI or no API key is available, so callers fall back to keyword
    search gracefully.
    """

    if config.llm_provider.lower() != "openai":
        return None
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or api_key.startswith("sk-placeholder"):
        return None

    model = config.openai_embedding_model

    def embed(text: str) -> list[float]:
        payload = json.dumps({"input": text, "model": model}).encode("utf-8")
        req = urllib.request.Request(
            "https://api.openai.com/v1/embeddings",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return data["data"][0]["embedding"]

    return embed
