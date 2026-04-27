"""Runtime configuration for AdaptiveAgent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency가 설치되면 실제 구현을 사용합니다.
    def load_dotenv(*_args: object, **_kwargs: object) -> bool:
        """python-dotenv 미설치 환경에서 설정 로드를 건너뜁니다."""

        return False


@dataclass(frozen=True)
class AgentConfig:
    """에이전트 실행에 필요한 설정값을 담습니다."""

    llm_provider: str = "ollama"
    ollama_model: str = "qwen2.5:1.5b"
    ollama_host: str | None = None
    openai_model: str = "gpt-4o-mini"
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
        """환경 변수와 .env 파일에서 설정을 로드합니다."""

        if env_file:
            load_dotenv(env_file)

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
            ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b"),
            ollama_host=os.getenv("OLLAMA_HOST") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
            grok_model=os.getenv("GROK_MODEL", "grok-beta"),
            language=language or os.getenv("ADAPTIVE_AGENT_LANGUAGE", "ko"),
            workspace_dir=workspace_dir,
            tool_library_dir=tool_library_dir,
            max_self_corrections=int(os.getenv("ADAPTIVE_AGENT_MAX_SELF_CORRECTIONS", "2")),
        )
