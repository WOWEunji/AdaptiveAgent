"""Adaptive agent orchestration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from adaptive_agent.config import AgentConfig
from adaptive_agent.llms.base import LLMClient
from adaptive_agent.llms.factory import create_llm_client
from adaptive_agent.state import AgentEvent, AgentState, Message, ToolSchema
from adaptive_agent.tools.executor import ToolExecutor
from adaptive_agent.tools.registry import ToolRegistry, create_default_registry


@dataclass
class AgentResponse:
    """Agent 실행 결과."""

    task: str
    output: Any
    tool_name: str | None = None
    action: str = "respond"
    events: list[AgentEvent] = field(default_factory=list)


_VALID_PLAN_ACTIONS = {"tool", "respond"}
_CLARIFICATION_ACTIONS = {
    "ask",
    "ask_human",
    "ask_user",
    "ask_user_input",
    "clarification",
    "clarify",
    "input_required",
    "request_clarification",
    "request_input",
    "request_user_input",
}


class AdaptiveAgent:
    """자연어 작업을 분석하고 필요한 툴을 실행하는 에이전트 골격."""

    def __init__(
        self,
        config: AgentConfig | None = None,
        llm_client: LLMClient | None = None,
        registry: ToolRegistry | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        self.config = config or AgentConfig.from_env()
        self.llm_client = llm_client or create_llm_client(self.config)
        self.registry = registry or create_default_registry(
            self.config.workspace_dir,
            tool_library_dir=self.config.tool_library_dir,
        )
        self.executor = executor or ToolExecutor(self.registry)

    def list_tools(self) -> list:
        """현재 등록된 툴 목록을 반환합니다."""

        return self.registry.list()

    def run(self, task: str) -> AgentResponse:
        """사용자 원문 task를 LLM 계획에 따라 수행합니다."""
        state = self._create_state()
        state.record_event("task_received", task=task)

        if task == "":
            state.record_event("clarification_requested", reason="empty_task")
            state.record_event("final_response_created", action="input_required")
            return AgentResponse(
                task=task,
                output="작업 내용을 입력해 주세요.",
                action="input_required",
                events=state.events,
            )

        state.history.append(Message(role="user", content=task))
        try:
            plan = self._plan_with_llm(task)
        except Exception as exc:
            state.failure_count += 1
            state.record_event("failure_classified", reason="external_provider_error")
            state.record_event("final_response_created", action="llm_error")
            return AgentResponse(
                task=task,
                output=f"LLM 호출 실패: {exc}",
                action="llm_error",
                events=state.events,
            )
        state.step_count += 1
        validation_error = plan.pop("_validation_error", None)
        if validation_error:
            state.record_event("plan_validation_failed", reason=validation_error)
        state.record_event("task_analyzed", action=plan.get("action", "respond"))
        response = self._run_normalized_plan(task, plan, state)
        if response is not None:
            return response

        if plan.get("needs_user_input"):
            state.record_event("clarification_requested", reason="llm_requested_user_input")
        state.record_event("final_response_created", action="llm")
        return AgentResponse(
            task=task,
            output=plan.get("response", ""),
            action="llm",
            events=state.events,
        )

    def run_tool(self, tool_name: str, arguments: dict[str, Any]):
        """명시적으로 지정된 툴을 실행합니다. 자연어 매칭을 수행하지 않습니다."""

        return self.executor.run(tool_name, arguments)

    def _run_normalized_plan(
        self,
        task: str,
        plan: dict[str, Any],
        state: AgentState,
    ) -> AgentResponse | None:
        """정규화된 LLM 계획을 실행하고 필요하면 제한된 self-correction을 수행합니다."""

        if plan.get("action") != "tool":
            return None

        tool_name = str(plan.get("tool_name") or "")
        arguments = self._normalized_arguments(plan)
        self._record_tool_spec(state, tool_name, arguments)
        result = self.run_tool(tool_name, arguments)
        self._record_tool_result(state, tool_name, result.success, result.error)
        if result.success:
            state.record_event("final_response_created", action="tool")
            return AgentResponse(
                task=task,
                output=result.output,
                tool_name=tool_name,
                action="tool",
                events=state.events,
            )

        state.failure_count += 1
        state.record_event("failure_classified", reason="tool_execution_error")
        current_plan = {"action": "tool", "tool_name": tool_name, "arguments": arguments}
        current_error = result.error
        current_output = result.output
        for attempt in range(1, self.config.max_self_corrections + 1):
            state.record_event(
                "self_correction_started",
                attempt=attempt,
                tool_name=tool_name,
                error=current_error,
            )
            try:
                corrected_plan = self._plan_correction_with_llm(
                    task,
                    current_plan,
                    error=current_error,
                    output=current_output,
                )
            except Exception as exc:
                state.record_event("failure_classified", reason="external_provider_error")
                current_error = f"LLM self-correction failed: {exc}"
                break

            validation_error = corrected_plan.pop("_validation_error", None)
            if validation_error:
                state.record_event("plan_validation_failed", reason=validation_error)
            if corrected_plan.get("action") != "tool":
                if corrected_plan.get("needs_user_input"):
                    state.record_event("clarification_requested", reason="self_correction_requested_user_input")
                state.record_event("final_response_created", action="llm")
                return AgentResponse(
                    task=task,
                    output=corrected_plan.get("response", ""),
                    action="llm",
                    events=state.events,
                )

            tool_name = str(corrected_plan.get("tool_name") or "")
            arguments = self._normalized_arguments(corrected_plan)
            self._record_tool_spec(state, tool_name, arguments)
            state.record_event("tool_reexecuted", tool_name=tool_name, attempt=attempt)
            result = self.run_tool(tool_name, arguments)
            self._record_tool_result(state, tool_name, result.success, result.error)
            if result.success:
                state.record_event("final_response_created", action="tool")
                return AgentResponse(
                    task=task,
                    output=result.output,
                    tool_name=tool_name,
                    action="tool",
                    events=state.events,
                )
            state.failure_count += 1
            state.record_event("failure_classified", reason="tool_execution_error")
            current_plan = {"action": "tool", "tool_name": tool_name, "arguments": arguments}
            current_error = result.error
            current_output = result.output

        state.record_event("final_response_created", action="tool_error")
        return AgentResponse(
            task=task,
            output=f"툴 실행 실패: {current_error}",
            tool_name=tool_name,
            action="tool_error",
            events=state.events,
        )

    def _normalized_arguments(self, plan: dict[str, Any]) -> dict[str, Any]:
        arguments = plan.get("arguments")
        return arguments if isinstance(arguments, dict) else {}

    def _record_tool_spec(self, state: AgentState, tool_name: str, arguments: dict[str, Any]) -> None:
        state.record_event(
            "tool_spec_created",
            tool_name=tool_name,
            argument_keys=sorted(str(key) for key in arguments),
        )
        code = arguments.get("code")
        if isinstance(code, str) and code:
            state.record_event("tool_code_created", tool_name=tool_name, code=code)
        if tool_name == "ask_human":
            state.record_event("clarification_requested", reason="llm_requested_human_input")
        state.record_event("tool_execution_requested", tool_name=tool_name)

    def _record_tool_result(
        self,
        state: AgentState,
        tool_name: str,
        success: bool,
        error: str | None,
    ) -> None:
        state.record_event("tool_executed", tool_name=tool_name, success=success)
        state.record_event(
            "tool_result_observed",
            tool_name=tool_name,
            success=success,
            has_error=error is not None,
        )

    def _plan_with_llm(self, task: str) -> dict[str, Any]:
        """LLM에게 원문 task와 툴 목록을 전달해 실행 계획을 받습니다."""

        response = self.llm_client.complete(self._build_prompt(task))
        parsed = self._loads_plan_json(response)

        return self._normalize_plan(parsed, fallback_response=response)

    def _plan_correction_with_llm(
        self,
        task: str,
        failed_plan: dict[str, Any],
        *,
        error: str | None,
        output: Any,
    ) -> dict[str, Any]:
        """실패한 툴 실행 관찰을 바탕으로 수정 계획을 요청합니다."""

        response = self.llm_client.complete(
            self._build_correction_prompt(
                task,
                failed_plan,
                error=error,
                output=output,
            )
        )
        try:
            parsed = self._loads_plan_json(response)
        except json.JSONDecodeError:
            parsed = response
        return self._normalize_plan(parsed, fallback_response=response)

    def _loads_plan_json(self, response: str) -> object:
        """LLM이 JSON 계획을 문자열로 한 번 더 감싸 반환해도 계획 객체로 복원합니다."""

        parsed: object = response
        for _ in range(3):
            if not isinstance(parsed, str):
                return parsed
            stripped = parsed.strip()
            if not stripped:
                return parsed
            try:
                parsed = json.loads(stripped)
                continue
            except json.JSONDecodeError:
                extracted = self._extract_json_object(stripped)
                if extracted == stripped:
                    return parsed
                try:
                    parsed = json.loads(extracted)
                    continue
                except json.JSONDecodeError:
                    return parsed
        return parsed

    def _extract_json_object(self, text: str) -> str:
        """응답 주변에 따옴표나 설명이 붙은 경우 첫 JSON object 후보를 추출합니다."""

        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return text
        return text[start : end + 1]

    def _normalize_plan(self, parsed: object, *, fallback_response: str) -> dict[str, Any]:
        """LLM 계획 JSON을 Agent가 실행 가능한 최소 계약으로 정규화합니다."""

        if not isinstance(parsed, dict):
            return {
                "action": "respond",
                "response": fallback_response,
                "_validation_error": "plan_not_object",
            }

        raw_action = parsed.get("action")
        if isinstance(raw_action, str) and self._is_clarification_action(raw_action):
            response = self._clarification_text(parsed, fallback_response)
            return self._ask_human_plan(response)

        if raw_action not in _VALID_PLAN_ACTIONS:
            if isinstance(raw_action, str) and self.registry.get(raw_action) is not None:
                parsed = {**parsed, "action": "tool", "tool_name": parsed.get("tool_name") or raw_action}
                raw_action = "tool"
            elif isinstance(parsed.get("tool_name"), str):
                parsed = {**parsed, "action": "tool"}
                raw_action = "tool"
            else:
                embedded_plan = self._loads_plan_json(str(parsed.get("response") or ""))
                if isinstance(embedded_plan, dict):
                    return self._normalize_plan(embedded_plan, fallback_response=fallback_response)
                return {
                    "action": "respond",
                    "response": str(parsed.get("response") or fallback_response),
                    "_validation_error": "unsupported_action",
                    "needs_user_input": bool(parsed.get("needs_user_input")),
                }

        if raw_action not in _VALID_PLAN_ACTIONS:
            return {
                "action": "respond",
                "response": str(parsed.get("response") or fallback_response),
                "_validation_error": "unsupported_action",
                "needs_user_input": bool(parsed.get("needs_user_input")),
            }

        if raw_action == "respond":
            response = parsed.get("response")
            if not isinstance(response, str):
                return {
                    "action": "respond",
                    "response": fallback_response,
                    "_validation_error": "invalid_response",
                }
            embedded_plan = self._loads_plan_json(response)
            if isinstance(embedded_plan, dict):
                embedded_action = embedded_plan.get("action")
                if embedded_action in _VALID_PLAN_ACTIONS or isinstance(embedded_plan.get("tool_name"), str):
                    return self._normalize_plan(embedded_plan, fallback_response=fallback_response)
            if bool(parsed.get("needs_user_input")):
                return self._ask_human_plan(response)
            return {
                "action": "respond",
                "response": response,
                "needs_user_input": bool(parsed.get("needs_user_input")),
            }

        tool_name = parsed.get("tool_name")
        if not isinstance(tool_name, str) or tool_name == "":
            return {
                "action": "respond",
                "response": "LLM 계획에 tool_name이 없어 툴을 실행하지 않았습니다.",
                "_validation_error": "invalid_tool_name",
            }

        arguments = parsed.get("arguments", {})
        if not isinstance(arguments, dict):
            return {
                "action": "respond",
                "response": "툴 실행 인자가 객체가 아니어서 실행하지 않았습니다.",
                "_validation_error": "invalid_tool_arguments",
            }
        arguments = self._normalize_tool_arguments(tool_name, arguments, parsed)
        return {"action": "tool", "tool_name": tool_name, "arguments": arguments}

    def _normalize_tool_arguments(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        parsed: dict[str, Any],
    ) -> dict[str, Any]:
        """LLM이 흔히 쓰는 인자 alias를 실제 내장 툴 계약으로 보정합니다."""

        normalized = dict(arguments)
        lang = normalized.get("arg_lang", parsed.get("arg_lang", parsed.get("language")))
        if "lang" not in normalized and isinstance(lang, str):
            normalized["lang"] = lang
        if tool_name != "code_execute":
            return normalized

        stdin_value = normalized.get(
            "arg_input",
            parsed.get(
                "arg_input",
                normalized.get(
                    "input",
                    parsed.get("input", normalized.get("input_text", parsed.get("input_text", normalized.get("stdin", parsed.get("stdin"))))),
                ),
            ),
        )
        if stdin_value is not None and isinstance(normalized.get("code"), str):
            normalized["code"] = self._inline_code_input(normalized["code"], stdin_value)
        return normalized

    def _inline_code_input(self, code: str, stdin_value: Any) -> str:
        """stdin을 지원하지 않는 code_execute에서 입력 alias를 안전하게 인라인합니다."""

        replacement = f"({stdin_value!r})"
        return code.replace("sys.stdin.read()", replacement).replace("input()", replacement)

    def _is_clarification_action(self, raw_action: str) -> bool:
        normalized = raw_action.strip().lower().replace("-", "_").replace(" ", "_")
        return normalized in _CLARIFICATION_ACTIONS or any(
            token in normalized for token in ("ask", "clarif", "input")
        )

    def _clarification_text(self, parsed: dict[str, Any], fallback_response: str) -> str:
        response = parsed.get("response")
        if isinstance(response, str):
            return response
        question = parsed.get("question")
        if isinstance(question, str):
            return question
        questions = parsed.get("questions")
        if isinstance(questions, list):
            return "\n".join(str(item) for item in questions)
        return fallback_response

    def _ask_human_plan(self, question: str) -> dict[str, Any]:
        return {
            "action": "tool",
            "tool_name": "ask_human",
            "arguments": {"questions": question},
        }

    def _build_prompt(self, task: str) -> str:
        """LLM에 전달할 기본 지시문을 구성합니다."""
        tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "category": tool.category,
                "usage": tool.usage,
            }
            for tool in self.registry.list()
        ]
        return (
            "You are AdaptiveAgent, a tool-using CLI agent. Keep the user's task exactly as "
            "provided: do not rewrite, trim, translate, change casing, or transform it. Decide "
            "using the original task only.\n"
            "Use tools for deterministic work, structured data processing, file operations, "
            "tests, or calculations. For JSON, CSV, or other structured data, prefer Python code "
            "through code_execute and use standard parsers such as json or csv; do not parse "
            "structured data with regular expressions or brittle string splitting. If a task is "
            "ambiguous, requires missing private credentials/data, or requires user permission, "
            "Use ask_human instead of guessing. Do not fabricate external data or credentials. "
            "For one-off deterministic analysis, generate general Python code that solves the "
            "class of task, not code tailored to a single expected answer.\n"
            "Return only JSON in one of these forms:\n"
            '{"action":"tool","tool_name":"<tool name>","arguments":{...}}\n'
            '{"action":"respond","response":"<answer>","needs_user_input":false}\n'
            '{"action":"respond","response":"<clarifying question>","needs_user_input":true}\n'
            f"Available tools: {json.dumps(tools, ensure_ascii=False)}\n"
            f"Original user task: {task}"
        )

    def _build_correction_prompt(
        self,
        task: str,
        failed_plan: dict[str, Any],
        *,
        error: str | None,
        output: Any,
    ) -> str:
        """자가 수정용 프롬프트를 구성합니다."""

        return (
            "You are AdaptiveAgent repairing a failed tool execution. Keep the original task "
            "unchanged and fix the general cause of the failure. Do not hard-code only the "
            "expected answer. If the task involves structured data, keep using standard parsers "
            "such as json or csv. Return only JSON using the same plan schema as before.\n"
            f"Original user task: {task}\n"
            f"Failed plan: {json.dumps(failed_plan, ensure_ascii=False)}\n"
            f"Observed error: {error}\n"
            f"Observed output: {json.dumps(output, ensure_ascii=False, default=str)}\n"
            "Return a corrected plan now."
        )

    def _create_state(self) -> AgentState:
        """현재 registry를 반영한 실행 상태를 만듭니다."""

        return AgentState(
            available_tools=[
                ToolSchema(
                    name=tool.name,
                    description=tool.description,
                    safety_level=tool.safety_level,
                    source="builtin",
                    validation_status="passed",
                )
                for tool in self.registry.list()
            ]
        )
