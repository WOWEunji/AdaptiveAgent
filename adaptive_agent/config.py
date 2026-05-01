"""Runtime configuration for AdaptiveAgent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency fallback
    def load_dotenv(*_args: object, **_kwargs: object) -> bool:
        """No-op dotenv loader for environments without python-dotenv."""

        return False


@dataclass(frozen=True)
class AgentConfig:
    """Runtime settings for AdaptiveAgent execution."""

    llm_provider: str = "ollama"
    ollama_model: str = "qwen3.5:2b"
    ollama_host: str | None = None
    openai_model: str = "gpt-5-nano"
    gemini_model: str = "gemini-2.5-flash-lite"
    anthropic_model: str = "claude-3-5-haiku-latest"
    grok_model: str = "grok-beta"
    language: str = "ko"
    workspace_dir: Path = Path.cwd()
    tool_library_dir: Path = Path.cwd() / ".adaptive_agent" / "tools"
    max_self_corrections: int = 2

    @classmethod
    def from_env(
        cls,
        env_file: str | os.PathLike[str] | None = ".env",
        *,
        llm_provider: str | None = None,
        language: str | None = None,
    ) -> "AgentConfig":
        """Load runtime settings from environment variables and .env."""

        if env_file:
            # Local .env values should override inherited shell defaults.
            load_dotenv(env_file, override=True)

        workspace_dir = Path(os.getenv("ADAPTIVE_AGENT_WORKSPACE", Path.cwd())).resolve()
        tool_library_dir = Path(
            os.getenv(
                "ADAPTIVE_AGENT_TOOL_LIBRARY",
                workspace_dir / ".adaptive_agent" / "tools",
            )
        ).resolve()

        return cls(
            llm_provider=llm_provider
            or os.getenv("ADAPTIVE_AGENT_LLM", os.getenv("LLM_PROVIDER", "ollama")),
            ollama_model=os.getenv("OLLAMA_MODEL", "qwen3.5:2b"),
            ollama_host=os.getenv("OLLAMA_HOST") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5-nano"),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
            grok_model=os.getenv("GROK_MODEL", "grok-beta"),
            language=language or os.getenv("ADAPTIVE_AGENT_LANGUAGE", "ko"),
            workspace_dir=workspace_dir,
            tool_library_dir=tool_library_dir,
            max_self_corrections=int(os.getenv("ADAPTIVE_AGENT_MAX_SELF_CORRECTIONS", "2")),
        )
