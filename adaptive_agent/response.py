"""Agent response and tool-attempt result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from adaptive_agent.state import AgentEvent


@dataclass
class AgentResponse:
    """Single AdaptiveAgent run result."""

    task: str
    output: Any
    tool_name: str | None = None
    action: str = "respond"
    events: list[AgentEvent] = field(default_factory=list)
    summary: str = ""
    needs_input: bool = False
    input_prompt: str = ""


@dataclass(frozen=True)
class _ToolAttemptOutcome:
    """One tool execution result + the data the retry loop needs to continue.

    ``response`` is the terminal :class:`AgentResponse` (when success follow-up
    short-circuited), or ``None`` when the router should keep going.
    ``last_*`` fields capture the inputs/observations of this attempt and are
    fed into the next correction prompt on failure.
    """

    success: bool
    response: AgentResponse | None
    last_plan: dict[str, Any]
    last_error: str | None
    last_output: Any
    tool_name: str
