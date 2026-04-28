"""Agent state and observable execution events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4


MessageRole = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class Message:
    """LLM 대화와 툴 관찰 결과를 보존하는 단일 메시지입니다."""

    role: MessageRole
    content: str
    tool_call_id: str | None = None


@dataclass(frozen=True)
class ToolSchema:
    """LLM과 실행기가 공유하는 툴 입력 계약입니다."""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    returns: dict[str, Any] = field(default_factory=dict)
    safety_level: str = "low"
    source: str = "builtin"
    validation_status: str = "unverified"


@dataclass(frozen=True)
class AgentEvent:
    """PR/CLI/테스트에서 확인할 수 있는 실행 이벤트입니다."""

    name: str
    details: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )


@dataclass
class AgentState:
    """한 번의 Agent 실행 세션에서 유지할 상태입니다."""

    session_id: str = field(default_factory=lambda: uuid4().hex)
    history: list[Message] = field(default_factory=list)
    events: list[AgentEvent] = field(default_factory=list)
    step_count: int = 0
    available_tools: list[ToolSchema] = field(default_factory=list)
    candidate_tools: list[ToolSchema] = field(default_factory=list)
    failure_count: int = 0
    summary: str = ""

    def record_event(self, name: str, **details: Any) -> None:
        """관찰 가능한 실행 이벤트를 순서대로 기록합니다."""

        self.events.append(AgentEvent(name=name, details=details))
