"""State-machine routing for AdaptiveAgent core execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from adaptive_agent.state import AgentState


@dataclass(frozen=True)
class RouterDependencies:
    """라우터가 기존 Agent core 기능을 호출하기 위한 최소 의존성입니다."""

    create_state: Callable[[], AgentState]
    plan_with_llm: Callable[[str], dict[str, Any]]
    run_normalized_plan: Callable[[str, dict[str, Any], AgentState], Any]
    make_response: Callable[..., Any]


class StateMachineRouter:
    """AgentState의 `next_node`를 기준으로 실행 흐름을 제어합니다."""

    def __init__(self, dependencies: RouterDependencies) -> None:
        self.dependencies = dependencies
        self.last_state: AgentState | None = None

    def run(self, task: str) -> Any:
        """현재 단일 계획 흐름을 명시적 라우터 경계 안에서 실행합니다."""

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
            plan = self.dependencies.plan_with_llm(task)
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

        state.step_count += 1
        state.current_plan = dict(plan)
        validation_error = plan.pop("_validation_error", None)
        if validation_error:
            state.record_event("plan_validation_failed", reason=validation_error)
        state.record_event("task_analyzed", action=plan.get("action", "respond"))

        state.next_node = "execute" if plan.get("action") == "tool" else "done"
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
