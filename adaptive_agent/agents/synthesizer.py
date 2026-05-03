"""Synthesizer agent: task-completion handler that produces a final answer and signals HITL save when needed."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from adaptive_agent.agents.base import AgentResult, BaseRoleAgent
from adaptive_agent.state import AgentState


class SynthesizerAgent(BaseRoleAgent):
    """Handles task completion: summarises execution output and flags code-save HITL when applicable.

    ``needs_code_save`` is ``True`` iff all three conditions hold:
    - ``state.last_tool_name == "code_execute"``
    - ``state.generated_code`` is non-empty
    - ``state.last_tool_result["success"] is True``

    The router inspects ``result.details["needs_code_save"]`` to decide whether to
    invoke the ``synthesize_code_save`` callback instead of returning immediately.
    """

    synthesize: Callable[[AgentState], str]

    def __init__(self, synthesize: Callable[[AgentState], str] | None = None) -> None:
        super().__init__(name="synthesize", role="synthesizer", prompt_template="synthesize.txt")
        self.synthesize = synthesize or _default_synthesize

    def run(self, state: AgentState) -> AgentResult:
        state.record_event("agent_started", agent_role=self.role, node=self.name)

        summary = self.synthesize(state)
        if isinstance(summary, str):
            state.summary = summary.strip()

        # Determine whether router must offer a code-save HITL step.
        needs_code_save = (
            state.last_tool_name == "code_execute"          # 마지막 툴이 코드 실행이어야 함
            and bool(getattr(state, "generated_code", ""))  # 저장할 코드가 실제로 생성되었어야 함
            and isinstance(state.last_tool_result, dict)    # 결과가 dict 형식(성공 키 확인 가능)이어야 함
            and state.last_tool_result.get("success") is True  # 실행이 성공으로 종료되었어야 함
        )

        state.record_event(
            "synthesis_created",
            agent_role=self.role,
            preview=state.summary[:120],
            needs_code_save=needs_code_save,
        )
        state.record_event("agent_finished", agent_role=self.role, node=self.name, next_node="done")
        state.next_node = "done"
        return AgentResult(
            next_node="done",
            details={"summary": state.summary, "needs_code_save": needs_code_save},
        )


def _default_synthesize(_state: AgentState) -> str:
    return ""
