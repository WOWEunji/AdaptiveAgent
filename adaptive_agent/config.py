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
    session_dir: Path = Path.cwd() / ".adaptive_agent" / "sessions"
    max_self_corrections: int = 2
    max_router_steps: int = 8
    max_parallel_perspectives: int = 3
    artifact_max_bytes: int = 10 * 1024 * 1024
    artifact_max_count: int = 1000
    web_fetch_allowed_domains: tuple[str, ...] = ()
    web_fetch_max_bytes: int = 1024 * 1024
    web_fetch_timeout_seconds: float = 10.0
    session_cleanup_enabled: bool = True
    session_max_age_days: int = 30
    session_max_count: int = 100
    ollama_timeout_seconds: float = 60.0
    ollama_num_predict: int = 256
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
        session_dir = Path(
            os.getenv(
                "ADAPTIVE_AGENT_SESSION_DIR",
                workspace_dir / ".adaptive_agent" / "sessions",
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
            session_dir=session_dir,
            max_self_corrections=int(os.getenv("ADAPTIVE_AGENT_MAX_SELF_CORRECTIONS", "2")),
            max_router_steps=int(os.getenv("ADAPTIVE_AGENT_MAX_ROUTER_STEPS", "8")),
            max_parallel_perspectives=int(os.getenv("ADAPTIVE_AGENT_MAX_PARALLEL_PERSPECTIVES", "3")),
            artifact_max_bytes=int(os.getenv("ADAPTIVE_AGENT_ARTIFACT_MAX_BYTES", str(10 * 1024 * 1024))),
            artifact_max_count=int(os.getenv("ADAPTIVE_AGENT_ARTIFACT_MAX_COUNT", "1000")),
            web_fetch_allowed_domains=tuple(
                d.strip()
                for d in os.getenv("ADAPTIVE_AGENT_WEB_FETCH_ALLOWED_DOMAINS", "").split(",")
                if d.strip()
            ),
            web_fetch_max_bytes=int(os.getenv("ADAPTIVE_AGENT_WEB_FETCH_MAX_BYTES", str(1024 * 1024))),
            web_fetch_timeout_seconds=float(os.getenv("ADAPTIVE_AGENT_WEB_FETCH_TIMEOUT_SECONDS", "10")),
            session_cleanup_enabled=os.getenv("ADAPTIVE_AGENT_SESSION_CLEANUP", "true").strip().lower() in {"1", "true", "yes", "on"},
            session_max_age_days=int(os.getenv("ADAPTIVE_AGENT_SESSION_MAX_AGE_DAYS", "30")),
            session_max_count=int(os.getenv("ADAPTIVE_AGENT_SESSION_MAX_COUNT", "100")),
            ollama_timeout_seconds=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60")),
            ollama_num_predict=int(os.getenv("OLLAMA_NUM_PREDICT", "256")),
            ollama_think=os.getenv("OLLAMA_THINK", "false").strip().lower() in {"1", "true", "yes", "on"},
        )
