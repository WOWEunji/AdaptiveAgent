"""StateMachineRouter 단위 전이 테스트.

라우터의 핵심 계약을 가짜 의존성으로 격리해 검증한다:
- 시작 노드는 retrieve
- retrieve → plan → (execute|code|done|approve) → critique → (done|plan|approve|error)
- max_steps 초과 시 router_error
- 알 수 없는 next_node 값은 router_error로 폴백
- plan agent 호출당 step_count 1 증가
- retrieve 단계는 step_count를 증가시키지 않음
- 의존성 콜러블이 예외를 던지면 llm_error 액션으로 회수
"""

from __future__ import annotations

import unittest
from typing import Any

from adaptive_agent.router import RouterDependencies, StateMachineRouter
from adaptive_agent.state import AgentState


def _make_response(**kwargs: Any) -> dict[str, Any]:
    return dict(kwargs)


def _create_state() -> AgentState:
    return AgentState()


class _RouterHarness:
    """가짜 의존성을 모아두는 헬퍼."""

    def __init__(
        self,
        *,
        plan: dict[str, Any] | None = None,
        plan_calls: list[dict[str, Any]] | None = None,
        critic: dict[str, Any] | None = None,
        critic_calls: list[dict[str, Any]] | None = None,
        executor_response: Any = None,
        executor_raises: BaseException | None = None,
        plan_raises: BaseException | None = None,
        max_steps: int = 8,
    ) -> None:
        self.plan_calls_made = 0
        self.critic_calls_made = 0
        self.executor_calls_made = 0
        self.plan_outputs = list(plan_calls or ([] if plan is None else [plan]))
        self.critic_outputs = list(critic_calls or ([] if critic is None else [critic]))
        self._executor_response = executor_response
        self._executor_raises = executor_raises
        self._plan_raises = plan_raises

        def plan_with_llm(_state: AgentState) -> dict[str, Any]:
            self.plan_calls_made += 1
            if self._plan_raises is not None:
                raise self._plan_raises
            return self.plan_outputs.pop(0) if self.plan_outputs else {"action": "respond", "response": "ok"}

        def critique_execution(_state: AgentState) -> dict[str, Any]:
            self.critic_calls_made += 1
            return self.critic_outputs.pop(0) if self.critic_outputs else {"verdict": "success", "next_node": "done"}

        def run_normalized_plan(_task: str, _plan: dict[str, Any], state: AgentState) -> Any:
            self.executor_calls_made += 1
            if self._executor_raises is not None:
                raise self._executor_raises
            state.last_tool_result = {"success": True, "output": "stub"}
            state.last_tool_name = _plan.get("tool_name")
            # 다음 라우팅을 critique로 보내고, 즉시 응답은 만들지 않는다 (None == 계속).
            state.next_node = "critique"
            return self._executor_response

        self.deps = RouterDependencies(
            create_state=_create_state,
            plan_with_llm=plan_with_llm,
            run_normalized_plan=run_normalized_plan,
            critique_execution=critique_execution,
            make_response=_make_response,
            max_steps=max_steps,
        )

    def router(self) -> StateMachineRouter:
        return StateMachineRouter(self.deps)


class RouterRunTest(unittest.TestCase):
    def test_empty_task_returns_input_required_without_invoking_plan(self) -> None:
        harness = _RouterHarness()
        result = harness.router().run("")

        self.assertEqual(result["action"], "input_required")
        self.assertEqual(harness.plan_calls_made, 0)

    def test_respond_plan_short_circuits_to_done(self) -> None:
        harness = _RouterHarness(plan={"action": "respond", "response": "hello"})
        result = harness.router().run("말해줘")

        self.assertEqual(result["action"], "llm")
        self.assertEqual(result["output"], "hello")
        self.assertEqual(harness.plan_calls_made, 1)
        self.assertEqual(harness.executor_calls_made, 0)
        self.assertEqual(harness.critic_calls_made, 0)

    def test_tool_plan_runs_executor_then_critique_then_done(self) -> None:
        harness = _RouterHarness(
            plan={"action": "tool", "tool_name": "echo", "arguments": {"task": "hi"}},
            critic={"verdict": "success", "next_node": "done"},
        )
        result = harness.router().run("툴 실행")

        self.assertEqual(result["action"], "tool")
        self.assertEqual(result["tool_name"], "echo")
        self.assertEqual(harness.plan_calls_made, 1)
        self.assertEqual(harness.executor_calls_made, 1)
        self.assertEqual(harness.critic_calls_made, 1)

    def test_critic_retry_routes_back_to_plan(self) -> None:
        harness = _RouterHarness(
            plan_calls=[
                {"action": "tool", "tool_name": "echo", "arguments": {"task": "1차"}},
                {"action": "respond", "response": "재계획 후 응답"},
            ],
            critic={"verdict": "retry", "next_node": "plan"},
        )
        result = harness.router().run("재계획 필요")

        self.assertEqual(result["action"], "llm")
        self.assertEqual(result["output"], "재계획 후 응답")
        self.assertEqual(harness.plan_calls_made, 2, "critic retry는 plan을 다시 호출해야 함")

    def test_critic_approval_required_routes_to_approve(self) -> None:
        harness = _RouterHarness(
            plan={"action": "tool", "tool_name": "x", "arguments": {}},
            critic={"verdict": "approval_required"},
        )
        result = harness.router().run("승인 필요")

        self.assertEqual(result["action"], "approval_required")

    def test_critic_error_verdict_returns_critic_error(self) -> None:
        harness = _RouterHarness(
            plan={"action": "tool", "tool_name": "x", "arguments": {}},
            critic={"verdict": "failed"},
        )
        result = harness.router().run("실패")

        self.assertEqual(result["action"], "critic_error")

    def test_step_limit_exceeded_emits_router_error(self) -> None:
        # 무한 retry 루프: critic이 plan으로 돌리고, plan은 다시 tool을 만든다.
        harness = _RouterHarness(
            plan_calls=[
                {"action": "tool", "tool_name": "echo", "arguments": {}}
                for _ in range(20)
            ],
            critic_calls=[
                {"verdict": "retry", "next_node": "plan"}
                for _ in range(20)
            ],
            max_steps=4,
        )
        router = harness.router()
        result = router.run("루프")

        self.assertEqual(result["action"], "router_error")
        reasons = [
            event.details.get("reason")
            for event in result["events"]
            if event.name == "failure_classified"
        ]
        self.assertIn("router_step_limit_exceeded", reasons)

    def test_plan_callable_exception_yields_llm_error(self) -> None:
        harness = _RouterHarness(plan_raises=RuntimeError("provider down"))
        result = harness.router().run("호출 실패")

        self.assertEqual(result["action"], "llm_error")
        self.assertIn("provider down", str(result["output"]))
        reasons = [
            event.details.get("reason")
            for event in result["events"]
            if event.name == "failure_classified"
        ]
        self.assertIn("external_provider_error", reasons)

    def test_unknown_next_node_falls_back_to_router_error(self) -> None:
        harness = _RouterHarness()
        router = harness.router()
        state = _create_state()
        state.user_task = "직접 주입"
        state.next_node = "banana"  # type: ignore[assignment]

        result = router.run_state(state)

        self.assertEqual(result["action"], "router_error")
        reasons = [
            event.details.get("reason")
            for event in state.events
            if event.name == "failure_classified"
        ]
        self.assertIn("unknown_next_node", reasons)

    def test_plan_step_count_increments_once_per_plan_call(self) -> None:
        harness = _RouterHarness(
            plan_calls=[
                {"action": "tool", "tool_name": "echo", "arguments": {}},
                {"action": "respond", "response": "끝"},
            ],
            critic={"verdict": "retry", "next_node": "plan"},
        )
        router = harness.router()
        router.run("증가 확인")

        self.assertIsNotNone(router.last_state)
        # plan agent가 두 번 호출되었으니 step_count 도 2여야 한다.
        self.assertEqual(router.last_state.step_count, 2)

    def test_retrieve_then_plan_records_task_received_first(self) -> None:
        harness = _RouterHarness(plan={"action": "respond", "response": "ok"})
        result = harness.router().run("이벤트 순서")
        event_names = [event.name for event in result["events"]]

        self.assertEqual(event_names[0], "task_received")
        self.assertIn("task_analyzed", event_names)
        self.assertEqual(event_names[-1], "final_response_created")


class RouterMatrixTest(unittest.TestCase):
    """critic verdict ↔ next_node 매핑 매트릭스."""

    def test_critic_verdict_to_next_node_matrix(self) -> None:
        cases = [
            ({"verdict": "success", "next_node": "done"}, "tool"),
            ({"verdict": "accepted"}, "tool"),
            ({"verdict": "pass"}, "tool"),
            ({"verdict": "retry", "next_node": "plan"}, "llm"),
            ({"verdict": "retry_needed", "next_node": "plan"}, "llm"),
            ({"verdict": "approval_required"}, "approval_required"),
            ({"verdict": "needs_human"}, "approval_required"),
            ({"verdict": "failed"}, "critic_error"),
        ]
        for critic_payload, expected_action in cases:
            with self.subTest(verdict=critic_payload):
                harness = _RouterHarness(
                    plan_calls=[
                        {"action": "tool", "tool_name": "echo", "arguments": {}},
                        {"action": "respond", "response": "재시도 응답"},
                    ],
                    critic=critic_payload,
                )
                result = harness.router().run("matrix 케이스")
                self.assertEqual(result["action"], expected_action)


if __name__ == "__main__":
    unittest.main()
