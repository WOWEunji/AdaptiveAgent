"""AdaptiveAgent 기본 동작 테스트."""

from __future__ import annotations

import unittest

from adaptive_agent.agent import AdaptiveAgent
from adaptive_agent.config import AgentConfig


class StubLLM:
    """테스트용 LLM 클라이언트."""

    def __init__(self, response: str = "LLM 응답") -> None:
        self.response = response
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


class AdaptiveAgentTest(unittest.TestCase):
    def test_empty_task_only_rejects_exact_empty_string(self) -> None:
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=StubLLM())

        result = agent.run("")

        self.assertEqual(result.output, "작업 내용을 입력해 주세요.")
        self.assertIsNone(result.tool_name)
        self.assertEqual([event.name for event in result.events], [
            "task_received",
            "clarification_requested",
            "final_response_created",
        ])

    def test_whitespace_task_is_preserved_for_llm(self) -> None:
        llm = StubLLM()
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        result = agent.run("   ")

        self.assertEqual(result.task, "   ")
        self.assertEqual(result.output, "LLM 응답")
        self.assertIn("Original user task:    ", llm.prompts[0])

    def test_natural_language_uses_llm_without_rule_matching(self) -> None:
        llm = StubLLM()
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        result = agent.run("echo 안녕하세요")

        self.assertEqual(result.output, "LLM 응답")
        self.assertIsNone(result.tool_name)
        self.assertIn("echo 안녕하세요", llm.prompts[0])

    def test_llm_json_plan_executes_tool(self) -> None:
        llm = StubLLM('{"action":"tool","tool_name":"echo","arguments":{"task":"원문 그대로"}}')
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        result = agent.run("사용자 원문")

        self.assertEqual(result.output, "원문 그대로")
        self.assertEqual(result.tool_name, "echo")
        self.assertEqual(result.action, "tool")
        self.assertEqual(
            [event.name for event in result.events],
            [
                "task_received",
                "task_analyzed",
                "tool_execution_requested",
                "tool_executed",
                "tool_result_observed",
                "final_response_created",
            ],
        )

    def test_tool_plan_without_tool_name_is_rejected(self) -> None:
        llm = StubLLM('{"action":"tool","arguments":{"task":"원문 그대로"}}')
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        result = agent.run("사용자 원문")

        self.assertEqual(result.action, "llm")
        self.assertEqual(result.output, "LLM 계획에 tool_name이 없어 툴을 실행하지 않았습니다.")
        self.assertIn("plan_validation_failed", [event.name for event in result.events])

    def test_unknown_plan_action_falls_back_to_response(self) -> None:
        llm = StubLLM('{"action":"unexpected","response":"대체 응답"}')
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        result = agent.run("사용자 원문")

        self.assertEqual(result.action, "llm")
        self.assertEqual(result.output, "대체 응답")
        self.assertIn("plan_validation_failed", [event.name for event in result.events])

    def test_tool_plan_arguments_must_be_object(self) -> None:
        llm = StubLLM('{"action":"tool","tool_name":"echo","arguments":["bad"]}')
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        result = agent.run("사용자 원문")

        self.assertEqual(result.output, "툴 실행 인자가 객체가 아니어서 실행하지 않았습니다.")
        self.assertEqual(result.action, "llm")
        self.assertIn("plan_validation_failed", [event.name for event in result.events])

    def test_tool_error_records_failure_event(self) -> None:
        llm = StubLLM('{"action":"tool","tool_name":"missing_tool","arguments":{}}')
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        result = agent.run("없는 툴 실행")

        self.assertEqual(result.action, "tool_error")
        self.assertEqual(result.tool_name, "missing_tool")
        self.assertIn("툴 실행 실패", result.output)
        self.assertIn("failure_classified", [event.name for event in result.events])

    def test_requirements_analysis_tool_returns_breakdown(self) -> None:
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=StubLLM())

        result = agent.run_tool("analyze_requirements", {})

        self.assertEqual(result.output["requirements"][0]["id"], "R1")
        self.assertIn("SkillX", result.output["requirements"][0]["reference"])

    def test_list_tools_includes_builtin_tools(self) -> None:
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=StubLLM())

        tool_names = {tool.name for tool in agent.list_tools()}

        self.assertIn("echo", tool_names)
        self.assertIn("list_files", tool_names)


if __name__ == "__main__":
    unittest.main()
