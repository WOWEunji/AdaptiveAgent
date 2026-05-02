"""Critic retry → Plan reflection round-trip 단위 테스트.

#14 — Critic이 retry verdict를 내면 reflection이 ``state.reflections``에
append되고, 그 다음 Plan 프롬프트의 ``{reflections}`` 슬롯에 실제로
들어가야 한다는 계약을 잠근다. 단순 reflection 누적뿐 아니라 LLM이 받는
프롬프트 내용까지 검증한다.
"""

from __future__ import annotations

import json
import re
import unittest
from typing import Any

from adaptive_agent.agent import AdaptiveAgent
from adaptive_agent.config import AgentConfig


class SequenceLLM:
    """호출 순서대로 응답을 반환하는 LLM 스텁."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            return '{"action":"respond","response":"sequence exhausted"}'
        return self.responses.pop(0)


def _classify_prompts(prompts: list[str]) -> list[str]:
    """Plan / critic / coder / correction 종류를 추정."""

    kinds = []
    for p in prompts:
        if "Critique" in p or "verdict" in p.casefold() and "current" in p.casefold():
            kinds.append("critic")
        elif "Original user task" in p and "Available tools" in p:
            kinds.append("plan")
        elif "Failed plan" in p or "Observed error" in p:
            kinds.append("correction")
        elif "QUALITY BAR" in p or "OUTPUT" in p and "JSON" in p:
            kinds.append("coder")
        else:
            kinds.append("other")
    return kinds


class CriticReflectionLoopTest(unittest.TestCase):
    def test_reflection_from_critic_appears_in_second_plan_prompt(self) -> None:
        marker = "REFLECTION_MARKER_경고_재계획_필요"
        llm = SequenceLLM(
            [
                # 1) first plan: tool plan
                '{"action":"tool","tool_name":"echo","arguments":{"task":"first attempt"}}',
                # 2) first critic: retry with reflection
                json.dumps(
                    {
                        "verdict": "retry",
                        "reason": "needs replanning",
                        "reflection": marker,
                        "next_node": "plan",
                    }
                ),
                # 3) second plan: should see reflection in {reflections} slot
                '{"action":"respond","response":"second plan done"}',
            ]
        )
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        result = agent.run("재계획 필요한 작업")

        # 최소한 plan→critic→plan 시퀀스가 일어나야 함 (3회 LLM 호출)
        self.assertGreaterEqual(len(llm.prompts), 3, "plan→critic→plan 시퀀스가 일어나야 함")

        # 첫 번째 plan 프롬프트에는 reflection이 없어야 함 (아직 수집 안 됨)
        first_plan_prompt = llm.prompts[0]
        self.assertNotIn(marker, first_plan_prompt, "1차 plan에는 reflection이 들어가면 안 됨")

        # 두 번째 plan 프롬프트(가장 마지막)에 reflection이 들어가야 함
        second_plan_prompt = llm.prompts[-1]
        self.assertIn(marker, second_plan_prompt, "2차 plan 프롬프트의 {reflections} 슬롯에 reflection이 포함되어야 함")

        # state.reflections에도 보존되어야 함
        last_state = agent.router.last_state
        self.assertIsNotNone(last_state)
        self.assertIn(marker, last_state.reflections)

        # 최종 응답은 second plan의 결과
        self.assertEqual(result.output, "second plan done")

    def test_empty_reflection_renders_empty_list_slot_without_leftovers(self) -> None:
        llm = SequenceLLM(
            [
                '{"action":"tool","tool_name":"echo","arguments":{"task":"x"}}',
                # critic의 reflection 빈 문자열
                json.dumps({"verdict": "retry", "reflection": "", "next_node": "plan"}),
                '{"action":"respond","response":"end"}',
            ]
        )
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        agent.run("empty reflection 시나리오")

        # 두 번째 plan 프롬프트에 미치환 자리표시자가 없어야 함
        second_plan = llm.prompts[-1]
        self.assertNotRegex(second_plan, r"\{[a-z_]+\}", "모든 자리표시자가 치환되어야 함")
        # reflections 슬롯은 빈 JSON 배열 [] 로 들어가야 함
        self.assertIn("[]", second_plan)

    def test_reflection_list_grows_in_order_across_two_retries(self) -> None:
        m1 = "REFLECTION_ONE_원인분석"
        m2 = "REFLECTION_TWO_재시도"
        llm = SequenceLLM(
            [
                '{"action":"tool","tool_name":"echo","arguments":{"task":"a"}}',
                json.dumps({"verdict": "retry", "reflection": m1, "next_node": "plan"}),
                '{"action":"tool","tool_name":"echo","arguments":{"task":"b"}}',
                json.dumps({"verdict": "retry", "reflection": m2, "next_node": "plan"}),
                '{"action":"respond","response":"finally done"}',
            ]
        )
        agent = AdaptiveAgent(
            config=AgentConfig(max_router_steps=20),
            llm_client=llm,
        )

        agent.run("두 번 retry")

        last_state = agent.router.last_state
        self.assertIsNotNone(last_state)
        # 순서가 보존되어야 함
        self.assertEqual(last_state.reflections, [m1, m2])

        # 마지막 plan 프롬프트엔 두 reflection 모두 포함되어야 함
        last_plan_prompt = llm.prompts[-1]
        self.assertIn(m1, last_plan_prompt)
        self.assertIn(m2, last_plan_prompt)

    def test_reflection_marker_round_trip_through_json_dump(self) -> None:
        # 한국어/특수문자가 JSON 직렬화 후에도 살아남는지 (json.dumps with ensure_ascii=False)
        marker = "한국어 reflection · 특수문자 \"인용\" 'apos' \\backslash"
        llm = SequenceLLM(
            [
                '{"action":"tool","tool_name":"echo","arguments":{"task":"x"}}',
                json.dumps({"verdict": "retry", "reflection": marker, "next_node": "plan"}, ensure_ascii=False),
                '{"action":"respond","response":"done"}',
            ]
        )
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)
        agent.run("특수문자 reflection")

        last_plan = llm.prompts[-1]
        # JSON 인코딩 차이를 허용하기 위해 핵심 토큰만 검증
        self.assertIn("한국어 reflection", last_plan)
        self.assertIn("특수문자", last_plan)


if __name__ == "__main__":
    unittest.main()
