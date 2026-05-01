"""State-machine routing for AdaptiveAgent core execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from adaptive_agent.nodes import PlanNode
from adaptive_agent.state import AgentState


@dataclass(frozen=True)
class RouterDependencies:
    """Injected callables required by StateMachineRouter."""

    create_state: Callable[[], AgentState]
    plan_with_llm: Callable[[str], dict[str, Any]]
    run_normalized_plan: Callable[[str, dict[str, Any], AgentState], Any]
    make_response: Callable[..., Any]


class StateMachineRouter:
    """State-machine boundary for one AdaptiveAgent execution."""

    def __init__(self, dependencies: RouterDependencies) -> None:
        self.dependencies = dependencies
        self.plan_node = PlanNode(dependencies.plan_with_llm)
        self.last_state: AgentState | None = None

    def run(self, task: str) -> Any:
        """Route one user task through the current plan-and-execute flow."""

        state = self.dependencies.create_state()
        self.last_state = state
        state.user_task = task
        state.next_node = "plan"
        state.record_event("task_received", task=task)

        if task == "":
            state.next_node = "done"
            state.record_event("clarification_requested", reason="empty_task")
            state.record_event("final_response_created", action="input_required")
            return self.dependencies.make_response(
                task=task,
                output="작업 내용을 입력해 주세요.",
                action="input_required",
                events=state.events,
            )

        state.append_message("user", task)
        try:
            node_result = self.plan_node.run(state)
        except Exception as exc:
            state.next_node = "error"
            state.failure_count += 1
            state.error_log = str(exc)
            state.record_event("failure_classified", reason="external_provider_error")
            state.record_event("final_response_created", action="llm_error")
            return self.dependencies.make_response(
                task=task,
                output=f"LLM 호출 실패: {exc}",
                action="llm_error",
                events=state.events,
            )

        plan = node_result.details.get("plan", {})
        if not isinstance(plan, dict):
            plan = {}
        response = self.dependencies.run_normalized_plan(task, plan, state)
        if response is not None:
            state.next_node = "done"
            return response

        if plan.get("needs_user_input"):
            state.next_node = "approve"
            state.record_event("clarification_requested", reason="llm_requested_user_input")
        else:
            state.next_node = "done"
        state.record_event("final_response_created", action="llm")
        return self.dependencies.make_response(
            task=task,
            output=plan.get("response", ""),
            action="llm",
            events=state.events,
        )
