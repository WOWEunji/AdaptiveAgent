"""Executor role agent implementation.

Phase 1 of #15: the bounded execution + self-correction logic moves out of
``agent.py`` and lives next to the role agent that owns it. ``ExecutorAgent``
itself remains a thin router-facing shim (so existing dependency-injection
wiring is untouched), but the loop primitives (``ToolAttemptOutcome``,
``execute_normalized_tool``, ``run_self_correction_loop``) are now module-level
functions that take an ``agent`` reference.

Phase 2-4 will keep tightening the boundary (move plan normalization and
prompt builders out, slim ``agent.py`` toward a thin facade).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from adaptive_agent.agents.base import AgentResult, BaseRoleAgent
from adaptive_agent.state import AgentState

if TYPE_CHECKING:
    from adaptive_agent.agent import AdaptiveAgent, AgentResponse


class ExecutorAgent(BaseRoleAgent):
    """Agent that executes the current normalized plan."""

    executor: Callable[[str, dict[str, Any], AgentState], Any]

    def __init__(self, executor: Callable[[str, dict[str, Any], AgentState], Any]) -> None:
        super().__init__(name="execute", role="executor", prompt_template="")
        self.executor = executor

    def run(self, state: AgentState) -> AgentResult:
        """Execute `AgentState.current_plan` and return the next node."""

        state.record_event("agent_started", agent_role=self.role, node=self.name)
        response = self.executor(state.user_task, state.current_plan, state)
        details: dict[str, object] = {}
        if response is not None:
            details["response"] = response
        state.record_event("agent_finished", agent_role=self.role, node=self.name, next_node=state.next_node)
        return AgentResult(next_node=state.next_node, details=details)


@dataclass(frozen=True)
class ToolAttemptOutcome:
    """One tool execution result + the data the retry loop needs to continue.

    ``response`` is the terminal :class:`AgentResponse` (when success follow-up
    short-circuited), or ``None`` when the router should keep going.
    ``last_*`` fields capture the inputs/observations of this attempt and are
    fed into the next correction prompt on failure.
    """

    success: bool
    response: "AgentResponse | None"
    last_plan: dict[str, Any]
    last_error: str | None
    last_output: Any
    tool_name: str


def execute_normalized_tool(
    agent: "AdaptiveAgent",
    task: str,
    plan: dict[str, Any],
    state: AgentState,
    *,
    retry_attempt: int | None = None,
) -> ToolAttemptOutcome:
    """Execute a single normalized ``tool`` plan and record events.

    On success delegates follow-up routing to
    :meth:`AdaptiveAgent._handle_successful_tool_result`; the returned response
    is either an :class:`AgentResponse` (terminal) or ``None`` (router
    continues).
    """

    tool_name = str(plan.get("tool_name") or "")
    arguments = agent._normalized_arguments(plan)
    state.last_tool_name = tool_name
    state.last_tool_arguments = dict(arguments)
    code = arguments.get("code")
    if isinstance(code, str):
        state.generated_code = code
    agent._record_tool_spec(state, tool_name, arguments)
    if retry_attempt is not None:
        state.record_event("tool_reexecuted", tool_name=tool_name, attempt=retry_attempt)

    result = agent.run_tool(tool_name, arguments, _state=state)
    state.last_tool_result = {
        "success": result.success,
        "output": result.output,
        "error": result.error,
    }
    agent._record_tool_result(state, tool_name, result.success, result.error)

    normalized_plan = {"action": "tool", "tool_name": tool_name, "arguments": arguments}
    if result.success:
        response = agent._handle_successful_tool_result(task, tool_name, result.output, state)
        return ToolAttemptOutcome(
            success=True,
            response=response,
            last_plan=normalized_plan,
            last_error=result.error,
            last_output=result.output,
            tool_name=tool_name,
        )

    state.failure_count += 1
    state.error_log = str(result.error or "")
    state.reflections.append(f"tool_execution_error:{tool_name}:{result.error}")
    state.record_event("failure_classified", reason="tool_execution_error")
    return ToolAttemptOutcome(
        success=False,
        response=None,
        last_plan=normalized_plan,
        last_error=result.error,
        last_output=result.output,
        tool_name=tool_name,
    )


def run_self_correction_loop(
    agent: "AdaptiveAgent",
    *,
    task: str,
    last_outcome: ToolAttemptOutcome,
    state: AgentState,
) -> "AgentResponse":
    """Replan and re-execute up to ``max_self_corrections`` times.

    Returns either a final ``tool_error`` response (loop exhausted or
    provider failure) or the corrected plan's terminal response.
    """

    from adaptive_agent.agent import AgentResponse  # avoid circular import at module load

    current_plan = last_outcome.last_plan
    current_error = last_outcome.last_error
    current_output = last_outcome.last_output
    tool_name = last_outcome.tool_name

    for attempt in range(1, agent.config.max_self_corrections + 1):
        state.record_event(
            "self_correction_started",
            attempt=attempt,
            tool_name=tool_name,
            error=current_error,
        )
        try:
            corrected_plan = agent._plan_correction_with_llm(
                task,
                current_plan,
                error=current_error,
                output=current_output,
            )
        except Exception as exc:
            state.record_event("failure_classified", reason="external_provider_error")
            current_error = f"LLM self-correction failed: {exc}"
            break

        validation_error = corrected_plan.pop("_validation_error", None)
        if validation_error:
            state.record_event("plan_validation_failed", reason=validation_error)
        if corrected_plan.get("action") != "tool":
            if corrected_plan.get("needs_user_input"):
                state.record_event(
                    "clarification_requested",
                    reason="self_correction_requested_user_input",
                )
            state.record_event("final_response_created", action="llm")
            return AgentResponse(
                task=task,
                output=corrected_plan.get("response", ""),
                action="llm",
                events=state.events,
            )

        outcome = execute_normalized_tool(
            agent,
            task,
            corrected_plan,
            state,
            retry_attempt=attempt,
        )
        if outcome.success:
            return outcome.response  # type: ignore[return-value]

        current_plan = outcome.last_plan
        current_error = outcome.last_error
        current_output = outcome.last_output
        tool_name = outcome.tool_name

    state.record_event("final_response_created", action="tool_error")
    state.next_node = "error"
    return AgentResponse(
        task=task,
        output=f"툴 실행 실패: {current_error}",
        tool_name=tool_name,
        action="tool_error",
        events=state.events,
        session_id=state.session_id,
    )
