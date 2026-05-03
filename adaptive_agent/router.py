"""State-machine routing for AdaptiveAgent core execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from adaptive_agent.logging import AgentLogger
    from adaptive_agent.response import AgentResponse

from adaptive_agent.agents import CoderAgent, CriticAgent, ExecutorAgent, LibrarianAgent, PlanAgent, SynthesizerAgent
from adaptive_agent.skills import SkillCatalog
from adaptive_agent.state import AgentState


@dataclass(frozen=True)
class RouterDependencies:
    """Injected callables required by StateMachineRouter."""

    create_state: Callable[[], AgentState]
    plan_with_llm: Callable[[AgentState], dict[str, Any]]
    executor_agent: ExecutorAgent
    critique_execution: Callable[[AgentState], dict[str, Any]]
    make_response: Callable[..., Any]
    retrieve_skills: Callable[[AgentState], list[dict[str, Any]]] | None = None
    code_with_llm: Callable[[AgentState], dict[str, Any]] | None = None
    synthesize_result: Callable[[AgentState], str] | None = None
    synthesize_code_save: Callable[[AgentState], AgentResponse] | None = None
    skill_catalog: SkillCatalog | None = None
    max_steps: int = 8
    logger: AgentLogger | None = None


class StateMachineRouter:
    """State-machine boundary for one AdaptiveAgent execution."""

    def __init__(self, dependencies: RouterDependencies) -> None:
        self.dependencies = dependencies
        self.librarian_agent = LibrarianAgent(
            dependencies.retrieve_skills,
            catalog=dependencies.skill_catalog,
        )
        self.plan_agent = PlanAgent(dependencies.plan_with_llm)
        self.coder_agent = CoderAgent(dependencies.code_with_llm)
        self.executor_agent = dependencies.executor_agent
        self.critic_agent = CriticAgent(dependencies.critique_execution)
        self.synthesizer_agent = SynthesizerAgent(dependencies.synthesize_result)
        self.last_state: AgentState | None = None

    def run(self, task: str) -> Any:
        """Route one user task through the current plan-and-execute flow."""

        state = self.dependencies.create_state()
        self.last_state = state
        state.user_task = task
        state.next_node = "retrieve"
        state.record_event("task_received", task=task)

        logger = self.dependencies.logger
        if logger:
            logger.on_task_start(task)

        if task == "":
            state.record_event("clarification_requested", reason="empty_task")
            return self._final_response(state, output="작업 내용을 입력해 주세요.", action="input_required")

        state.append_message("user", task)
        return self.run_state(state)

    def run_state(self, state: AgentState) -> Any:
        """Continue routing an existing state."""

        self.last_state = state

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
        logger = self.dependencies.logger
        node = state.next_node

        if node == "retrieve":
            if logger: logger.on_node_enter(node, state)
            self.librarian_agent.run(state)
            if logger: logger.on_node_exit(node, state)
            return None

        if node == "plan":
            if logger: logger.on_node_enter(node, state)
            self.plan_agent.run(state)
            if logger: logger.on_node_exit(node, state)
            # ask_human 연속 루프 가드: 직전 툴이 ask_human이거나 history에 [확인 요청]이 있으면 respond로 전환
            _prev_ask = state.last_tool_name == "ask_human" or any(
                getattr(m, "role", "") == "assistant" and "[확인 요청]" in str(getattr(m, "content", ""))
                for m in (state.history or [])
            )
            if (
                state.current_plan.get("tool_name") == "ask_human"
                and _prev_ask
            ):
                resp = str(
                    (state.current_plan.get("arguments") or {}).get("questions")
                    or state.current_plan.get("response")
                    or "필요한 정보가 없어 진행할 수 없습니다. 구체적인 내용을 직접 입력해주세요."
                )
                state.current_plan = {"action": "respond", "response": resp}
                state.next_node = "done"
            if state.next_node in {"done", "approve"}:
                return self._response_from_current_plan(state)
            return None

        if node == "code":
            if logger: logger.on_node_enter(node, state)
            self.coder_agent.run(state)
            if logger: logger.on_node_exit(node, state)
            return None

        if node == "execute":
            if logger: logger.on_node_enter(node, state)
            node_result = self.executor_agent.run(state)
            if logger: logger.on_node_exit(node, state)
            response = node_result.details.get("response")
            if response is not None:
                return response
            return None

        if node == "critique":
            if logger: logger.on_node_enter(node, state)
            self.critic_agent.run(state)
            if logger: logger.on_node_exit(node, state)
            if state.next_node == "done":
                state.next_node = "synthesize"
                return None
            if state.next_node == "approve":
                return self._final_response(state, output=state.last_tool_result or {}, action="approval_required")
            if state.next_node == "error":
                return self._final_response(state, output=state.error_log or "Critic rejected execution.", action="critic_error")
            return None

        if node == "synthesize":
            if logger: logger.on_node_enter(node, state)
            result = self.synthesizer_agent.run(state)
            if logger: logger.on_node_exit(node, state)

            if result.details.get("needs_code_save") and self.dependencies.synthesize_code_save:
                return self.dependencies.synthesize_code_save(state)

            # needs_code_save=True이지만 synthesize_code_save 콜백이 없거나,
            # needs_code_save=False인 경우 모두 여기서 폴백한다 — 의도된 동작.
            return self._tool_response_from_state(state)

        if node == "done":
            return self._response_from_current_plan(state)
        if node == "approve":
            return self._response_from_current_plan(state, action="approval_required")
        if node == "error":
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
            summary=state.summary,
        )

    def _final_response(
        self,
        state: AgentState,
        *,
        output: Any,
        action: str,
        tool_name: str | None = None,
        summary: str = "",
    ) -> Any:
        state.next_node = "done" if state.next_node != "error" else "error"
        state.record_event("final_response_created", action=action)
        if self.dependencies.logger:
            self.dependencies.logger.on_final(output, action)
        return self.dependencies.make_response(
            task=state.user_task,
            output=output,
            tool_name=tool_name,
            action=action,
            events=state.events,
            summary=summary,
        )
