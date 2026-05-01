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


class SequenceLLM:
    """호출 순서대로 응답을 반환하는 테스트용 LLM 클라이언트."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            return '{"action":"respond","response":"응답 없음"}'
        return self.responses.pop(0)


class FailingLLM:
    """테스트용 실패 LLM 클라이언트."""

    def complete(self, _prompt: str) -> str:
        raise ValueError("LLM 연결 실패")


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

    def test_llm_error_returns_structured_response(self) -> None:
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=FailingLLM())

        result = agent.run("OpenAI 연결 확인")

        self.assertEqual(result.action, "llm_error")
        self.assertEqual(result.output, "LLM 호출 실패: LLM 연결 실패")
        self.assertIn("failure_classified", [event.name for event in result.events])

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
                "tool_spec_created",
                "tool_execution_requested",
                "tool_executed",
                "tool_result_observed",
                "final_response_created",
            ],
        )

    def test_code_tool_plan_records_created_code_event(self) -> None:
        llm = StubLLM(
            '{"action":"tool","tool_name":"code_execute","arguments":'
            '{"code":"print(225)","expected_output":"225"}}'
        )
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        result = agent.run("구조화 데이터를 계산해줘")

        event_names = [event.name for event in result.events]
        self.assertEqual(result.action, "tool")
        self.assertIn("tool_spec_created", event_names)
        self.assertIn("tool_code_created", event_names)

    def test_prompt_instructs_general_structured_data_tool_use(self) -> None:
        llm = StubLLM()
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        agent.run("아래 JSON을 분석해줘: []")

        prompt = llm.prompts[0]
        self.assertIn("Use tools for deterministic work", prompt)
        self.assertIn("standard parsers such as json or csv", prompt)
        self.assertIn("not code tailored to a single expected answer", prompt)
        self.assertIn("Use ask_human", prompt)

    def test_unsupported_clarification_action_is_normalized_to_ask_human(self) -> None:
        llm = StubLLM('{"action":"clarify","response":"어떤 데이터인지 알려주세요."}')
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        result = agent.run("데이터 정리해줘")

        event_names = [event.name for event in result.events]
        self.assertEqual(result.action, "tool")
        self.assertEqual(result.tool_name, "ask_human")
        self.assertIn("clarification_requested", event_names)
        self.assertIn("pending_human_input", str(result.output))

    def test_tool_error_can_self_correct_and_reexecute(self) -> None:
        llm = SequenceLLM(
            [
                '{"action":"tool","tool_name":"code_execute","arguments":{"code":"print(missing_name)"}}',
                '{"action":"tool","tool_name":"code_execute","arguments":{"code":"print(\\"fixed\\")"}}',
            ]
        )
        agent = AdaptiveAgent(config=AgentConfig(max_self_corrections=1), llm_client=llm)

        result = agent.run("코드 실행 오류를 고쳐줘")

        event_names = [event.name for event in result.events]
        self.assertEqual(result.action, "tool")
        self.assertIn("self_correction_started", event_names)
        self.assertIn("tool_reexecuted", event_names)
        self.assertIn("fixed", str(result.output))
        self.assertEqual(len(llm.prompts), 2)

    def test_double_encoded_json_plan_is_executed(self) -> None:
        llm = StubLLM(
            '"{\\"action\\":\\"tool\\",\\"tool_name\\":\\"echo\\",'
            '\\"arguments\\":{\\"task\\":\\"decoded\\"}}"'
        )
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        result = agent.run("이중 인코딩된 계획")

        self.assertEqual(result.action, "tool")
        self.assertEqual(result.tool_name, "echo")
        self.assertEqual(result.output, "decoded")

    def test_json_plan_inside_response_field_is_executed(self) -> None:
        llm = StubLLM(
            '{"action":"respond","response":"{\\"action\\":\\"tool\\",'
            '\\"tool_name\\":\\"echo\\",\\"arguments\\":{\\"task\\":\\"nested\\"}}"}'
        )
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        result = agent.run("응답 필드에 들어간 계획")

        self.assertEqual(result.action, "tool")
        self.assertEqual(result.tool_name, "echo")
        self.assertEqual(result.output, "nested")

    def test_tool_name_action_and_arg_aliases_are_normalized(self) -> None:
        llm = StubLLM(
            '{"action":"code_execute","arguments":{'
            '"code":"import sys\\nprint(sys.stdin.read())",'
            '"arg_input":"alias input","arg_lang":"python"}}'
        )
        agent = AdaptiveAgent(config=AgentConfig(max_self_corrections=0), llm_client=llm)

        result = agent.run("비표준 툴 계획")

        self.assertEqual(result.action, "tool")
        self.assertEqual(result.tool_name, "code_execute")
        self.assertIn("alias input", str(result.output))

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
        self.assertIn("code_execute", tool_names)
        self.assertIn("ask_human", tool_names)


if __name__ == "__main__":
    unittest.main()
