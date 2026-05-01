"""State-machine routing for AdaptiveAgent core execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from adaptive_agent.nodes import CriticNode, ExecuteNode, PlanNode
from adaptive_agent.state import AgentState


@dataclass(frozen=True)
class RouterDependencies:
    """Injected callables required by StateMachineRouter."""

    create_state: Callable[[], AgentState]
    plan_with_llm: Callable[[str], dict[str, Any]]
    run_normalized_plan: Callable[[str, dict[str, Any], AgentState], Any]
    critique_execution: Callable[[AgentState], dict[str, Any]]
    make_response: Callable[..., Any]
    max_steps: int = 8


class StateMachineRouter:
    """State-machine boundary for one AdaptiveAgent execution."""

    def __init__(self, dependencies: RouterDependencies) -> None:
        self.dependencies = dependencies
        self.plan_node = PlanNode(dependencies.plan_with_llm)
        self.execute_node = ExecuteNode(dependencies.run_normalized_plan)
        self.critic_node = CriticNode(dependencies.critique_execution)
        self.last_state: AgentState | None = None

    def run(self, task: str) -> Any:
        """Route one user task through the current plan-and-execute flow."""

        state = self.dependencies.create_state()
        self.last_state = state
        state.user_task = task
        state.next_node = "plan"
        state.record_event("task_received", task=task)

        if task == "":
            state.record_event("clarification_requested", reason="empty_task")
            return self._final_response(state, output="작업 내용을 입력해 주세요.", action="input_required")

        state.append_message("user", task)

        for _step in range(self.dependencies.max_steps):
            try:
                response = self._run_next_node(state)
            except Exception as exc:
                state.next_node = "error"
                state.failure_count += 1
                state.error_log = str(exc)
                state.record_event("failure_classified", reason="external_provider_error")
                return self._final_response(state, output=f"LLM 호출 실패: {exc}", action="llm_error")
            if response is not None:
                return response

        state.next_node = "error"
        state.failure_count += 1
        state.error_log = "router_step_limit_exceeded"
        state.record_event("failure_classified", reason="router_step_limit_exceeded")
        return self._final_response(state, output="라우터 실행 단계 한도를 초과했습니다.", action="router_error")

    def _run_next_node(self, state: AgentState) -> Any | None:
        if state.next_node == "plan":
            self.plan_node.run(state)
            if state.next_node in {"done", "approve"}:
                return self._response_from_current_plan(state)
            return None

        if state.next_node == "execute":
            node_result = self.execute_node.run(state)
            response = node_result.details.get("response")
            if response is not None:
                return response
            return None

        if state.next_node == "critique":
            self.critic_node.run(state)
            if state.next_node == "done":
                return self._tool_response_from_state(state)
            if state.next_node == "approve":
                return self._final_response(state, output=state.last_tool_result or {}, action="approval_required")
            if state.next_node == "error":
                return self._final_response(state, output=state.error_log or "Critic rejected execution.", action="critic_error")
            return None

        if state.next_node == "done":
            return self._response_from_current_plan(state)
        if state.next_node == "approve":
            return self._response_from_current_plan(state, action="approval_required")
        if state.next_node == "error":
            return self._final_response(state, output=state.error_log, action="error")

        state.next_node = "error"
        state.record_event("failure_classified", reason="unknown_next_node")
        return self._final_response(state, output="알 수 없는 라우터 상태입니다.", action="router_error")

    def _response_from_current_plan(self, state: AgentState, action: str = "llm") -> Any:
        plan = state.current_plan
        if plan.get("needs_user_input"):
            state.record_event("clarification_requested", reason="llm_requested_user_input")
            action = "approval_required"
        return self._final_response(state, output=plan.get("response", ""), action=action)

    def _tool_response_from_state(self, state: AgentState) -> Any:
        result = state.last_tool_result or {}
        return self._final_response(
            state,
            output=result.get("output"),
            action="tool",
            tool_name=state.last_tool_name,
        )

    def _final_response(
        self,
        state: AgentState,
        *,
        output: Any,
        action: str,
        tool_name: str | None = None,
    ) -> Any:
        state.next_node = "done" if state.next_node != "error" else "error"
        state.record_event("final_response_created", action=action)
        return self.dependencies.make_response(
            task=state.user_task,
            output=output,
            tool_name=tool_name,
            action=action,
            events=state.events,
        )
