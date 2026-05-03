"""Executor role agent — owns the tool execution and self-correction loop."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable

from adaptive_agent.agents.base import AgentResult, BaseRoleAgent
from adaptive_agent.response import AgentResponse, _ToolAttemptOutcome
from adaptive_agent.state import AgentState


@dataclass(frozen=True)
class ExecutorDependencies:
    """Callables and config injected into :class:`ExecutorAgent`.

    These are the only external entry-points the executor needs; all other
    behaviour lives inside ``ExecutorAgent`` itself.
    """

    run_tool: Callable[..., Any]
    """AdaptiveAgent.run_tool(tool_name, arguments, _state=state)"""

    handle_success: Callable[..., AgentResponse | None]
    """AdaptiveAgent._handle_successful_tool_result(task, tool_name, output, state)"""

    plan_correction: Callable[..., dict[str, Any]]
    """AdaptiveAgent._plan_correction_with_llm(task, failed_plan, error=..., output=...)"""

    max_self_corrections: int
    logger: "Any | None" = None


class ExecutorAgent(BaseRoleAgent):
    """Agent that executes the current normalized plan with bounded self-correction."""

    def __init__(self, deps: ExecutorDependencies) -> None:
        super().__init__(name="execute", role="executor", prompt_template="")
        self._deps = deps

    def run(self, state: AgentState) -> AgentResult:
        """Execute ``AgentState.current_plan`` through the full correction loop."""

        state.record_event("agent_started", agent_role=self.role, node=self.name)
        response = self._run_normalized_plan(state.user_task, state.current_plan, state)
        details: dict[str, object] = {}
        if response is not None:
            details["response"] = response
        state.record_event("agent_finished", agent_role=self.role, node=self.name, next_node=state.next_node)
        return AgentResult(next_node=state.next_node, details=details)

    # ------------------------------------------------------------------
    # Core execution pipeline
    # ------------------------------------------------------------------

    def _run_normalized_plan(
        self,
        task: str,
        plan: dict[str, Any],
        state: AgentState,
    ) -> AgentResponse | None:
        """Execute a normalized tool plan with bounded self-correction.

        First attempt + (optional) bounded retry loop. Handles ``action ==
        "parallel"`` by running all sub-actions concurrently via
        :class:`~concurrent.futures.ThreadPoolExecutor`.
        """

        if plan.get("action") == "parallel":
            return self._run_parallel_plan(task, plan, state)

        if plan.get("action") != "tool":
            return None

        outcome = self._execute_normalized_tool(task, plan, state)
        if outcome.success:
            return outcome.response

        return self._run_self_correction_loop(task=task, last_outcome=outcome, state=state)

    def _run_parallel_plan(
        self,
        task: str,
        plan: dict[str, Any],
        state: AgentState,
    ) -> AgentResponse:
        """Run all tool sub-actions in the plan concurrently.

        Each sub-action is executed in its own thread. Results are stored in
        ``state.parallel_results``; the response ``output`` is the same list.
        State mutation (``last_tool_name`` etc.) is skipped to avoid races —
        callers should inspect ``parallel_results`` directly.
        """

        actions: list[dict[str, Any]] = [
            a for a in plan.get("actions", [])
            if isinstance(a, dict) and a.get("action") == "tool"
        ]
        slots: list[dict[str, Any] | None] = [None] * len(actions)

        def _run_one(index: int, sub_plan: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            tool_name = str(sub_plan.get("tool_name") or "")
            arguments = sub_plan.get("arguments") if isinstance(sub_plan.get("arguments"), dict) else {}
            try:
                result = self._deps.run_tool(tool_name, arguments, _state=None)
                return index, {
                    "tool_name": tool_name,
                    "success": result.success,
                    "output": result.output,
                    "error": result.error,
                }
            except Exception as exc:
                return index, {
                    "tool_name": tool_name,
                    "success": False,
                    "output": None,
                    "error": str(exc),
                }

        max_workers = min(len(actions), 8) if actions else 1
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_run_one, i, action): i for i, action in enumerate(actions)}
            for future in as_completed(futures):
                idx, result = future.result()
                slots[idx] = result

        results = [r for r in slots if r is not None]
        state.parallel_results = results
        all_success = bool(results) and all(r.get("success") for r in results)
        state.next_node = "critique" if all_success else "error"
        success_count = sum(1 for r in results if r.get("success"))
        state.record_event(
            "parallel_execution_completed",
            action_count=len(actions),
            success_count=success_count,
        )
        state.record_event("final_response_created", action="parallel")
        return AgentResponse(
            task=task,
            output=results,
            action="parallel",
            events=state.events,
        )

    def _execute_normalized_tool(
        self,
        task: str,
        plan: dict[str, Any],
        state: AgentState,
        *,
        retry_attempt: int | None = None,
    ) -> _ToolAttemptOutcome:
        """Execute a single normalized ``tool`` plan and record events.

        On success delegates follow-up routing to the injected
        ``handle_success`` callable; the returned response is either an
        :class:`AgentResponse` (terminal) or ``None`` (router continues).
        """

        tool_name = str(plan.get("tool_name") or "")
        arguments = self._normalized_arguments(plan)
        state.last_tool_name = tool_name
        state.last_tool_arguments = dict(arguments)
        code = arguments.get("code")
        if isinstance(code, str):
            state.generated_code = code
        self._record_tool_spec(state, tool_name, arguments)
        if retry_attempt is not None:
            state.record_event("tool_reexecuted", tool_name=tool_name, attempt=retry_attempt)

        result = self._deps.run_tool(tool_name, arguments, _state=state)
        state.last_tool_result = {
            "success": result.success,
            "output": result.output,
            "error": result.error,
        }
        self._record_tool_result(state, tool_name, result.success, result.error)

        normalized_plan = {"action": "tool", "tool_name": tool_name, "arguments": arguments}
        if result.success:
            response = self._deps.handle_success(task, tool_name, result.output, state)
            return _ToolAttemptOutcome(
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
        return _ToolAttemptOutcome(
            success=False,
            response=None,
            last_plan=normalized_plan,
            last_error=result.error,
            last_output=result.output,
            tool_name=tool_name,
        )

    def _run_self_correction_loop(
        self,
        *,
        task: str,
        last_outcome: _ToolAttemptOutcome,
        state: AgentState,
    ) -> "AgentResponse | None":
        """Replan and re-execute up to ``max_self_corrections`` times.

        Returns either a final ``tool_error`` response (loop exhausted or
        provider failure) or the corrected plan's terminal response.
        """

        current_plan = last_outcome.last_plan
        current_error = last_outcome.last_error
        current_output = last_outcome.last_output
        tool_name = last_outcome.tool_name

        for attempt in range(1, self._deps.max_self_corrections + 1):
            state.record_event(
                "self_correction_started",
                attempt=attempt,
                tool_name=tool_name,
                error=current_error,
            )
            if self._deps.logger:
                self._deps.logger.on_self_correction(attempt, current_error or "", tool_name)
            try:
                corrected_plan = self._deps.plan_correction(
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

            outcome = self._execute_normalized_tool(
                task,
                corrected_plan,
                state,
                retry_attempt=attempt,
            )
            if outcome.success:
                state.error_log = ""
                return outcome.response

            current_plan = outcome.last_plan
            current_error = outcome.last_error
            current_output = outcome.last_output
            tool_name = outcome.tool_name

        # 저장된(retrieved) 스킬이 실패한 경우 한 번만 재계획 기회를 준다.
        # 내장 툴(tool_approve 등) 실패나 이미 재계획한 경우는 에러로 종료한다.
        state.error_log = current_error or "tool_execution_failed"
        retrieved_names = {
            s.get("name") if isinstance(s, dict) else str(s)
            for s in (state.retrieved_skills or [])
        }
        if tool_name in retrieved_names and state.failure_count < 2:
            state.failure_count += 1
            state.record_event("failure_classified", reason="retrieved_skill_failed_replanning")
            state.next_node = "plan"
            return None

        state.record_event("final_response_created", action="tool_error")
        state.next_node = "error"
        return AgentResponse(
            task=task,
            output=f"툴 실행 실패: {current_error}",
            tool_name=tool_name,
            action="tool_error",
            events=state.events,
        )

    # ------------------------------------------------------------------
    # Helpers (moved from AdaptiveAgent)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalized_arguments(plan: dict[str, Any]) -> dict[str, Any]:
        arguments = plan.get("arguments")
        return arguments if isinstance(arguments, dict) else {}

    @staticmethod
    def _record_tool_spec(state: AgentState, tool_name: str, arguments: dict[str, Any]) -> None:
        state.record_event(
            "tool_spec_created",
            tool_name=tool_name,
            argument_keys=sorted(str(key) for key in arguments),
        )
        code = arguments.get("code")
        if isinstance(code, str) and code:
            state.record_event("tool_code_created", tool_name=tool_name, code=code)
        if tool_name == "ask_human":
            state.record_event("clarification_requested", reason="llm_requested_human_input")
        state.record_event("tool_execution_requested", tool_name=tool_name)

    @staticmethod
    def _record_tool_result(
        state: AgentState,
        tool_name: str,
        success: bool,
        error: str | None,
    ) -> None:
        state.record_event("tool_executed", tool_name=tool_name, success=success)
        state.record_event(
            "tool_result_observed",
            tool_name=tool_name,
            success=success,
            has_error=error is not None,
        )
