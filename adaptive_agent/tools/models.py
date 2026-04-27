"""Tool model definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Tool:
    """에이전트가 호출할 수 있는 툴 정의입니다."""

    name: str
    description: str
    keywords: tuple[str, ...]
    handler: Callable[[dict[str, Any]], "ToolExecutionResult"]


@dataclass(frozen=True)
class ToolExecutionResult:
    """툴 실행 결과를 표준화합니다."""

    success: bool
    output: Any
    error: str | None = None
