"""AdaptiveAgent 기본 동작 테스트."""

from __future__ import annotations

import json
import unittest
import tempfile
from pathlib import Path

from adaptive_agent.agents import CoderAgent, CriticAgent, ExecutorAgent, LibrarianAgent, PlanAgent
from adaptive_agent.agent import AdaptiveAgent
from adaptive_agent.config import AgentConfig
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

        self.assertEqual(result.action, "input_required")
        self.assertIsNone(result.tool_name)
        self.assertTrue(str(result.output).strip(), "clarification 메시지는 비어있지 않아야 합니다")
        event_names = {event.name for event in result.events}
        for required in {"task_received", "clarification_requested", "final_response_created"}:
            self.assertIn(required, event_names)
        clarification_events = [e for e in result.events if e.name == "clarification_requested"]
        self.assertTrue(clarification_events, "clarification_requested 이벤트가 있어야 합니다")
        self.assertEqual(clarification_events[0].details.get("reason"), "empty_task")

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

        self.assertEqual(result.action, "llm")
        self.assertIsNone(result.tool_name)
        self.assertEqual(len(llm.prompts), 1, "툴 매칭 없이 LLM이 정확히 한 번 호출되어야 합니다")
        self.assertIn("echo 안녕하세요", llm.prompts[0])

    def test_llm_error_returns_structured_response(self) -> None:
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=FailingLLM())

        result = agent.run("OpenAI 연결 확인")

        self.assertEqual(result.action, "llm_error")
        self.assertIn("LLM 연결 실패", str(result.output), "원인 예외 메시지가 응답에 노출되어야 합니다")
        failure_events = [e for e in result.events if e.name == "failure_classified"]
        self.assertTrue(failure_events)
        self.assertEqual(failure_events[0].details.get("reason"), "external_provider_error")

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

        prompt = loader.render(
            "plan.txt",
            available_tools="[]",
            task="원문 유지",
            retrieved_skills="[]",
            reflections="[]",
            last_tool_result="null",
        )

        self.assertIn("[]", prompt)
        self.assertIn("원문 유지", prompt)
        self.assertNotIn("{available_tools}", prompt)
        self.assertNotIn("{task}", prompt)

    def test_role_prompt_files_are_loadable(self) -> None:
        loader = PromptLoader()

        for template_name in ("coder.txt", "critic.txt"):
            prompt = loader.load(template_name)
            self.assertGreater(len(prompt.strip()), 0)

    def test_role_agents_expose_separate_contracts(self) -> None:
        agents = [
            LibrarianAgent(),
            PlanAgent(lambda _state: {"action": "respond", "response": "ok"}),
            CoderAgent(),
            ExecutorAgent(lambda _task, _plan, _state: None),
            CriticAgent(),
        ]

        self.assertEqual([agent.role for agent in agents], ["librarian", "planner", "coder", "executor", "critic"])

    def test_coder_agent_routes_to_error_when_required_fields_missing(self) -> None:
        from adaptive_agent.state import AgentState as _State

        cases = [
            ("name 누락", {"description": "d", "code": "def run(a): pass\n"}, ["name"]),
            ("description 누락", {"name": "t", "code": "def run(a): pass\n"}, ["description"]),
            ("code 누락", {"name": "t", "description": "d"}, ["code"]),
            ("빈 문자열은 누락 취급", {"name": "  ", "description": "d", "code": "def run(a): pass\n"}, ["name"]),
            (
                "비-문자열 code는 누락 취급",
                {"name": "t", "description": "d", "code": {"raw": "stuff"}},
                ["code"],
            ),
        ]
        for label, plan_arguments, expected_missing in cases:
            with self.subTest(case=label):
                state = _State()
                state.user_task = "create tool"
                state.current_plan = {
                    "action": "tool",
                    "tool_name": "tool_create",
                    "arguments": plan_arguments,
                }
                # coder LLM이 아무 보강도 못 했다고 가정
                agent = CoderAgent(coder=lambda _s: {})

                result = agent.run(state)

                self.assertEqual(result.next_node, "error")
                self.assertEqual(result.status, "invalid_arguments")
                self.assertEqual(result.details["missing_fields"], expected_missing)
                invalid_events = [e for e in state.events if e.name == "coder_arguments_invalid"]
                self.assertTrue(invalid_events, "coder_arguments_invalid 이벤트가 있어야 합니다")
                self.assertEqual(invalid_events[0].details["missing_fields"], expected_missing)

    def test_coder_agent_proceeds_to_execute_when_llm_supplies_missing_fields(self) -> None:
        from adaptive_agent.state import AgentState as _State

        state = _State()
        state.user_task = "create tool"
        state.current_plan = {
            "action": "tool",
            "tool_name": "tool_create",
            "arguments": {"name": "hello_tool", "description": "Greets"},
        }
        agent = CoderAgent(coder=lambda _s: {"code": "def run(arguments):\n    return {}\n"})

        result = agent.run(state)

        self.assertEqual(result.next_node, "execute")
        self.assertEqual(state.current_plan["arguments"]["name"], "hello_tool")
        self.assertIn("def run", state.current_plan["arguments"]["code"])
        self.assertIn("tool_code_created", [e.name for e in state.events])

    def test_unsupported_clarification_action_is_normalized_to_ask_human(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            llm = StubLLM('{"action":"clarify","response":"어떤 데이터인지 알려주세요."}')
            agent = AdaptiveAgent(
                config=AgentConfig(
                    workspace_dir=workspace,
                    tool_library_dir=workspace / ".adaptive_agent" / "tools",
                    session_dir=workspace / ".adaptive_agent" / "sessions",
                ),
                llm_client=llm,
            )

            result = agent.run("데이터 정리해줘")

            event_names = [event.name for event in result.events]
            self.assertEqual(result.action, "tool")
            self.assertEqual(result.tool_name, "ask_human")
            self.assertIn("clarification_requested", event_names)
            self.assertIn("pending_human_input", str(result.output))
            self.assertIsNotNone(result.pending)
            self.assertTrue((workspace / ".adaptive_agent" / "sessions" / f"{result.session_id}.json").exists())

    def test_pending_session_can_resume_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            agent = AdaptiveAgent(
                config=AgentConfig(
                    workspace_dir=workspace,
                    tool_library_dir=workspace / ".adaptive_agent" / "tools",
                    session_dir=workspace / ".adaptive_agent" / "sessions",
                ),
                llm_client=SequenceLLM(
                    [
                        '{"action":"clarify","response":"어떤 데이터인지 알려주세요."}',
                        '{"action":"respond","response":"CSV 데이터로 진행합니다."}',
                    ]
                ),
            )
            result = agent.run("데이터 정리해줘")

            resumed = agent.resume(str(result.session_id), user_input="CSV 데이터")

            self.assertEqual(resumed.action, "llm")
            self.assertEqual(resumed.output, "CSV 데이터로 진행합니다.")
            self.assertIn("session_resumed", [event.name for event in resumed.events])
            with self.assertRaises(ValueError):
                agent.resume(str(result.session_id), user_input="다시 입력")

    def test_generated_tool_flow_validates_and_requires_approval_before_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            llm = SequenceLLM(
                [
                    '{"action":"tool","tool_name":"tool_create","arguments":'
                    '{"name":"hello_tool","description":"Greets","code":"def run(arguments):\\n    return {\\"hello\\": arguments.get(\\"name\\", \\"world\\")}\\n"}}'
                ]
            )
            agent = AdaptiveAgent(
                config=AgentConfig(
                    workspace_dir=workspace,
                    tool_library_dir=workspace / ".adaptive_agent" / "tools",
                    session_dir=workspace / ".adaptive_agent" / "sessions",
                ),
                llm_client=llm,
            )

            result = agent.run("인사 툴 만들어줘")

            self.assertEqual(result.action, "approval_required")
            self.assertEqual(result.tool_name, "tool_validate")
            self.assertIsNotNone(result.pending)
            pending_path = workspace / ".adaptive_agent" / "sessions" / f"{result.session_id}.json"
            pending_text = pending_path.read_text(encoding="utf-8")
            self.assertIn("tool_approve", pending_text)
            self.assertNotIn("def run", pending_text)

            approved = agent.resume(str(result.session_id), approve=True)

            self.assertEqual(approved.action, "tool")
            self.assertEqual(approved.tool_name, "tool_approve")
            self.assertEqual(approved.output["catalog"]["name"], "hello_tool")

    def test_failed_approval_resume_keeps_session_pending_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            agent = AdaptiveAgent(
                config=AgentConfig(
                    workspace_dir=workspace,
                    tool_library_dir=workspace / ".adaptive_agent" / "tools",
                    session_dir=workspace / ".adaptive_agent" / "sessions",
                    max_self_corrections=0,
                ),
                llm_client=SequenceLLM(
                    [
                        '{"action":"tool","tool_name":"tool_create","arguments":'
                        '{"name":"changed_tool","description":"Changes","code":"def run(arguments):\\n    return {\\"ok\\": True}\\n"}}'
                    ]
                ),
            )
            result = agent.run("변경 감지 툴 만들어줘")
            (workspace / ".adaptive_agent" / "tools" / "changed_tool.py").write_text(
                "def run(arguments):\n    return {'changed': True}\n",
                encoding="utf-8",
            )

            failed = agent.resume(str(result.session_id), approve=True)

            self.assertEqual(failed.action, "tool_error")
            pending_path = workspace / ".adaptive_agent" / "sessions" / f"{result.session_id}.json"
            pending = json.loads(pending_path.read_text(encoding="utf-8"))
            self.assertEqual(pending["status"], "pending")

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
        correction_prompt = llm.prompts[1]
        self.assertIn("코드 실행 오류를 고쳐줘", correction_prompt, "원본 작업 원문이 보정 프롬프트에 포함되어야 합니다")
        self.assertIn("print(missing_name)", correction_prompt, "실패한 plan 컨텍스트가 보정 프롬프트에 포함되어야 합니다")
        self.assertNotRegex(correction_prompt, r"\{[a-z_]+\}", "보정 프롬프트의 모든 자리표시자가 치환되어야 합니다")

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

        # 자유 영역(영문 안내 문구)은 검증하지 않고, 변수 치환 계약만 확인
        self.assertNotRegex(prompt, r"\{[a-z_]+\}", "모든 자리표시자가 치환되어야 합니다")
        self.assertIn("원본 작업", prompt)
        self.assertIn('{"action":"tool"}', prompt)
        self.assertIn("NameError", prompt)
        self.assertIn('{"stdout":""}', prompt)


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
        self.assertIsNone(result.tool_name, "tool_name 누락 plan은 실행되지 않아야 합니다")
        self.assertTrue(str(result.output).strip(), "거부 사유 메시지는 비어있지 않아야 합니다")
        validation_events = [e for e in result.events if e.name == "plan_validation_failed"]
        self.assertTrue(validation_events, "plan_validation_failed 이벤트가 있어야 합니다")
        self.assertIn("tool_name", str(validation_events[0].details).lower())

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

        self.assertEqual(result.action, "llm")
        self.assertIsNone(result.tool_name, "잘못된 arguments 타입은 툴 실행으로 이어지지 않아야 합니다")
        self.assertTrue(str(result.output).strip())
        validation_events = [e for e in result.events if e.name == "plan_validation_failed"]
        self.assertTrue(validation_events, "plan_validation_failed 이벤트가 있어야 합니다")
        self.assertIn("arguments", str(validation_events[0].details).lower())

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

    def test_user_task_input_variations_are_preserved(self) -> None:
        cases = [
            ("한국어", "데이터를 정리해줘"),
            ("이모지", "🎉 ship it 🚀"),
            ("긴 입력", "x" * 8_000),
            ("앞뒤 공백 보존", "  앞 뒤  공백  "),
            ("탭과 줄바꿈", "first\tline\nsecond line"),
            ("JSON처럼 보이는 평문", '{"action":"실제로는 평문"}'),
            ("따옴표 혼합", "\"양쪽\" '단일' 따옴표"),
            ("이스케이프 시퀀스 평문", "raw \\n not a newline"),
        ]
        for label, task in cases:
            with self.subTest(case=label):
                llm = StubLLM()
                agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

                result = agent.run(task)

                self.assertEqual(result.task, task, "원문 task가 응답에 그대로 보존되어야 합니다")
                self.assertGreaterEqual(len(llm.prompts), 1)
                self.assertIn(task, llm.prompts[0], "원문 task가 plan 프롬프트에 포함되어야 합니다")

    def test_invalid_plan_json_variations_fall_back_safely(self) -> None:
        cases = [
            ("완전 비-JSON", "그냥 자유 텍스트 응답"),
            ("미완성 JSON", '{"action":"tool",'),
            ("내부 깨진 JSON", '{"action":"tool", "tool_name": "echo", arguments: {}}'),
            ("빈 문자열", ""),
            ("JSON null", "null"),
            ("JSON 배열", "[1, 2, 3]"),
            ("숫자만", "42"),
        ]
        for label, llm_response in cases:
            with self.subTest(case=label):
                agent = AdaptiveAgent(
                    config=AgentConfig(max_self_corrections=0),
                    llm_client=StubLLM(llm_response),
                )

                result = agent.run("계획 요청")

                # 깨진 JSON은 router_error/exception이 아닌, 정상적인 fallback action으로 회수되어야 함
                self.assertIn(
                    result.action,
                    {"llm", "tool", "input_required", "tool_error"},
                    f"잘못된 응답 '{label}'이 안전한 action으로 폴백되어야 합니다",
                )

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
