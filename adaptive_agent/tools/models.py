"""Tool model definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


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


@dataclass(frozen=True)
class ToolExecutionResult:
    """Standard result envelope for tool execution."""

    success: bool
    output: Any
    error: str | None = None
