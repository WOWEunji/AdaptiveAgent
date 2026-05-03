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
    openai_embedding_model: str = "text-embedding-3-small"
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-4.1-nano"
    coder_provider: str = ""   # 빈 문자열이면 llm_provider를 따름
    coder_model: str = ""      # 빈 문자열이면 해당 provider 기본 모델을 따름

    language: str = "ko"
    workspace_dir: Path = Path.cwd()
    tool_library_dir: Path = Path.cwd() / ".adaptive_agent" / "tools"
    log_dir: Path = Path.cwd() / ".adaptive_agent" / "logs"
    max_self_corrections: int = 2
    max_router_steps: int = 12
    artifact_dir: Path = Path.cwd() / ".adaptive_agent" / "artifacts"
    ollama_port: int = 11434
    ollama_timeout_seconds: float = 60.0
    ollama_num_predict: int = 6144
    ollama_think: bool = False

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
        artifact_dir = Path(
            os.getenv(
                "ADAPTIVE_AGENT_ARTIFACT_DIR",
                workspace_dir / ".adaptive_agent" / "artifacts",
            )
        ).resolve()
        log_dir = Path(
            os.getenv(
                "ADAPTIVE_AGENT_LOG_DIR",
                workspace_dir / ".adaptive_agent" / "logs",
            )
        ).resolve()

        return cls(
            llm_provider=llm_provider
            or os.getenv("ADAPTIVE_AGENT_LLM", os.getenv("LLM_PROVIDER", "ollama")),
            ollama_model=os.getenv("OLLAMA_MODEL", "qwen3.5:2b"),
            ollama_host=os.getenv("OLLAMA_HOST") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5-nano"),
            openai_embedding_model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            openrouter_model=os.getenv("OPENROUTER_MODEL", "openai/gpt-4.1-nano"),
            coder_provider=os.getenv("ADAPTIVE_AGENT_CODER_LLM", ""),
            coder_model=os.getenv("ADAPTIVE_AGENT_CODER_MODEL", ""),

            language=language or os.getenv("ADAPTIVE_AGENT_LANGUAGE", "ko"),
            workspace_dir=workspace_dir,
            tool_library_dir=tool_library_dir,
            artifact_dir=artifact_dir,
            log_dir=log_dir,
            max_self_corrections=int(os.getenv("ADAPTIVE_AGENT_MAX_SELF_CORRECTIONS", "2")),
            max_router_steps=int(os.getenv("ADAPTIVE_AGENT_MAX_ROUTER_STEPS", "12")),
            ollama_port=int(os.getenv("OLLAMA_PORT", "11434")),
            ollama_timeout_seconds=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60")),
            ollama_num_predict=int(os.getenv("OLLAMA_NUM_PREDICT", "6144")),
            ollama_think=os.getenv("OLLAMA_THINK", "false").strip().lower() in {"1", "true", "yes", "on"},
        )
