"""AdaptiveAgent 기본 동작 테스트."""

from __future__ import annotations

import unittest

from adaptive_agent.agent import AdaptiveAgent
from adaptive_agent.config import AgentConfig
from adaptive_agent.nodes import CoderNode, CriticNode
from adaptive_agent.prompts import PromptLoader
from adaptive_agent.router import StateMachineRouter


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


class AlternatingRetryLLM:
    """테스트용 반복 LLM 클라이언트."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if len(self.prompts) % 2 == 1:
            return '{"action":"tool","tool_name":"echo","arguments":{"task":"retry target"}}'
        return '{"verdict":"retry","reason":"retry requested","reflection":"try again","next_node":"plan"}'


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
        event_names = [event.name for event in result.events]
        self.assertLess(event_names.index("task_analyzed"), event_names.index("tool_execution_requested"))
        self.assertLess(event_names.index("tool_result_observed"), event_names.index("execution_critiqued"))
        self.assertLess(event_names.index("execution_critiqued"), event_names.index("final_response_created"))
        self.assertIsInstance(agent.router, StateMachineRouter)
        self.assertIsNotNone(agent.router.last_state)
        self.assertEqual(agent.router.last_state.user_task, "사용자 원문")
        self.assertEqual(agent.router.last_state.current_plan["tool_name"], "echo")
        self.assertEqual(agent.router.last_state.next_node, "done")

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

    def test_plan_prompt_receives_original_task(self) -> None:
        llm = StubLLM()
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        agent.run("아래 JSON을 분석해줘: []")

        prompt = llm.prompts[0]
        self.assertIn("아래 JSON을 분석해줘: []", prompt)
        self.assertNotIn("{task}", prompt)
        self.assertNotIn("{available_tools}", prompt)

    def test_default_plan_prompt_file_renders_dynamic_context(self) -> None:
        loader = PromptLoader()

        prompt = loader.render("plan.txt", available_tools="[]", task="원문 유지")

        self.assertIn("[]", prompt)
        self.assertIn("원문 유지", prompt)
        self.assertNotIn("{available_tools}", prompt)
        self.assertNotIn("{task}", prompt)

    def test_role_prompt_files_are_loadable(self) -> None:
        loader = PromptLoader()

        for template_name in ("coder.txt", "critic.txt"):
            prompt = loader.load(template_name)
            self.assertGreater(len(prompt.strip()), 0)

    def test_role_nodes_point_to_role_prompt_templates(self) -> None:
        nodes = [CoderNode(), CriticNode()]

        self.assertEqual([node.name for node in nodes], ["code", "critique"])
        self.assertEqual([node.prompt_template for node in nodes], ["coder.txt", "critic.txt"])

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
        self.assertIn("execution_critiqued", event_names)
        self.assertIn("fixed", str(result.output))
        self.assertGreaterEqual(len(llm.prompts), 2)
        self.assertIn("repairing a failed tool execution", llm.prompts[1])
        self.assertIn("Original user task: 코드 실행 오류를 고쳐줘", llm.prompts[1])
        self.assertIn("Failed plan:", llm.prompts[1])
        self.assertIn("Observed error:", llm.prompts[1])

    def test_critic_retry_routes_back_to_plan(self) -> None:
        llm = SequenceLLM(
            [
                '{"action":"tool","tool_name":"echo","arguments":{"task":"first"}}',
                '{"verdict":"retry","reason":"needs another plan","reflection":"retry once","next_node":"plan"}',
                '{"action":"final_answer","answer":"재계획 완료"}',
            ]
        )
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        result = agent.run("재계획이 필요한 작업")

        event_names = [event.name for event in result.events]
        self.assertEqual(result.action, "llm")
        self.assertEqual(result.output, "재계획 완료")
        self.assertGreaterEqual(event_names.count("task_analyzed"), 2)
        self.assertIn("execution_critiqued", event_names)

    def test_router_step_limit_stops_repeating_loops(self) -> None:
        agent = AdaptiveAgent(config=AgentConfig(max_router_steps=3), llm_client=AlternatingRetryLLM())

        result = agent.run("반복되는 작업")

        self.assertEqual(result.action, "router_error")
        self.assertIn("router_step_limit_exceeded", [event.details.get("reason") for event in result.events])

    def test_default_correction_prompt_file_renders_failure_context(self) -> None:
        loader = PromptLoader()

        prompt = loader.render(
            "correction.txt",
            task="원본 작업",
            failed_plan='{"action":"tool"}',
            error="NameError",
            output='{"stdout":""}',
        )

        self.assertIn("Return only JSON using the same plan schema as before", prompt)
        self.assertIn("Original user task: 원본 작업", prompt)
        self.assertIn('Failed plan: {"action":"tool"}', prompt)
        self.assertIn("Observed error: NameError", prompt)
        self.assertIn('Observed output: {"stdout":""}', prompt)


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

    def test_markdown_fenced_json_plan_is_executed(self) -> None:
        llm = StubLLM(
            "```json\n"
            '{"action":"tool","tool_name":"echo","arguments":{"task":"fenced"}}'
            "\n```"
        )
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        result = agent.run("마크다운 코드블록 계획")

        self.assertEqual(result.action, "tool")
        self.assertEqual(result.tool_name, "echo")
        self.assertEqual(result.output, "fenced")

    def test_direct_clarification_text_is_normalized_to_ask_human(self) -> None:
        llm = StubLLM("Please provide more details about the cleanup process.")
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        result = agent.run("데이터 정리해줘")

        self.assertEqual(result.action, "tool")
        self.assertEqual(result.tool_name, "ask_human")
        self.assertIn("pending_human_input", str(result.output))

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

    def test_use_tool_action_is_normalized_to_tool_plan(self) -> None:
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=StubLLM())

        plan = agent._normalize_plan(
            {"action": "use_tool", "tool_name": "echo", "arguments": {"task": "ok"}},
            fallback_response="fallback",
        )

        self.assertEqual(plan, {"action": "tool", "tool_name": "echo", "arguments": {"task": "ok"}})

    def test_create_tool_action_maps_top_level_fields_to_tool_create(self) -> None:
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=StubLLM())

        plan = agent._normalize_plan(
            {
                "action": "create_tool",
                "name": "hello_tool",
                "description": "Greets a user",
                "code": "def run(arguments):\n    return arguments\n",
            },
            fallback_response="fallback",
        )

        self.assertEqual(plan["action"], "tool")
        self.assertEqual(plan["tool_name"], "tool_create")
        self.assertEqual(plan["arguments"]["name"], "hello_tool")
        self.assertEqual(plan["arguments"]["description"], "Greets a user")
        self.assertIn("def run", plan["arguments"]["code"])

    def test_approve_tool_action_maps_to_tool_approve(self) -> None:
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=StubLLM())

        plan = agent._normalize_plan(
            {"action": "approve_tool", "name": "hello_tool"},
            fallback_response="fallback",
        )

        self.assertEqual(plan, {"action": "tool", "tool_name": "tool_approve", "arguments": {"name": "hello_tool"}})

    def test_final_answer_action_is_normalized_to_response(self) -> None:
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=StubLLM())

        plan = agent._normalize_plan(
            {"action": "final_answer", "answer": "완료"},
            fallback_response="fallback",
        )

        self.assertEqual(plan["action"], "respond")
        self.assertEqual(plan["response"], "완료")

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

    def test_agent_state_blueprint_fields_have_defaults(self) -> None:
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=StubLLM())

        state = agent._create_state()

        self.assertEqual(state.user_task, "")
        self.assertEqual(state.retrieved_skills, [])
        self.assertEqual(state.current_plan, {})
        self.assertEqual(state.generated_code, "")
        self.assertIsNone(state.last_tool_result)
        self.assertEqual(state.reflections, [])
        self.assertEqual(state.next_node, "plan")


if __name__ == "__main__":
    unittest.main()
