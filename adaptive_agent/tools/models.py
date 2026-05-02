"""Tool model definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


# R5 (requirements_breakdown.md) 스킬 라이브러리 분류 — Tool은 이 셋 중
# 정확히 하나에 속해야 한다. ``category``는 자유 설명 레이블(filesystem,
# execution 등)이고, ``skill_class``는 planning context에 노출되는 정책
# 분류다.
#
# - planning : 다음 행동을 결정하기 위한 메타 도구 (요구사항 분해, 후보
#              검색, 다른 도구 추천 등). LLM이 plan 단계에서 자주 호출.
# - functional: 특정 기능을 실제로 수행하는 도구 (파일 I/O, HTTP, 코드
#              실행, 메모리 등). 보통 도메인 효과를 만든다.
# - atomic   : 더 이상 쪼갤 수 없는 최소 단위 (echo, ask_human 같은
#              순수 입출력 brick).
SKILL_CLASS_PLANNING = "planning"
SKILL_CLASS_FUNCTIONAL = "functional"
SKILL_CLASS_ATOMIC = "atomic"
SKILL_CLASSES: frozenset[str] = frozenset(
    {SKILL_CLASS_PLANNING, SKILL_CLASS_FUNCTIONAL, SKILL_CLASS_ATOMIC}
)


@dataclass(frozen=True)
class Tool:
    """Callable tool definition exposed to planning and execution."""

    name: str
    description: str
    handler: Callable[[dict[str, Any]], "ToolExecutionResult"]
    category: str = "function"
    requires_llm: bool = False
    safety_level: str = "low"
    usage: str = ""
    source: str = "builtin"
    parameters: dict[str, Any] | None = None
    returns: dict[str, Any] | None = None
    validation_status: str = "unverified"
    skill_class: str = SKILL_CLASS_FUNCTIONAL

    def __post_init__(self) -> None:
        if self.skill_class not in SKILL_CLASSES:
            raise ValueError(
                f"Tool {self.name!r}: skill_class must be one of "
                f"{sorted(SKILL_CLASSES)}, got {self.skill_class!r}"
            )


@dataclass(frozen=True)
class ToolExecutionResult:
    """Standard result envelope for tool execution."""

    success: bool
    output: Any
    error: str | None = None
