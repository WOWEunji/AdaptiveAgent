"""Adaptive agent orchestration."""

from __future__ import annotations

import json
from typing import Any

from adaptive_agent.agents.executor import ExecutorAgent, ExecutorDependencies
from adaptive_agent.config import AgentConfig
from adaptive_agent.llms.base import LLMClient
from adaptive_agent.llms.factory import create_embedding_fn, create_llm_client
from adaptive_agent.prompts import PromptLoader
from adaptive_agent.response import AgentResponse
from adaptive_agent.router import RouterDependencies, StateMachineRouter
from adaptive_agent.sessions import SessionStore
from adaptive_agent.skills import SkillCatalog
from adaptive_agent.state import AgentState, ToolSchema
from adaptive_agent.tools.executor import ToolExecutor
from adaptive_agent.tools.registry import ToolRegistry, create_default_registry


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
    """CLI-oriented agent facade for planning, tool execution, and correction."""

    def __init__(
        self,
        config: AgentConfig | None = None,
        llm_client: LLMClient | None = None,
        registry: ToolRegistry | None = None,
        executor: ToolExecutor | None = None,
        prompt_loader: PromptLoader | None = None,
    ) -> None:
        self.config = config or AgentConfig.from_env()
        self.llm_client = llm_client or create_llm_client(self.config)
        self.registry = registry or create_default_registry(
            self.config.workspace_dir,
            tool_library_dir=self.config.tool_library_dir,
            artifact_dir=self.config.artifact_dir,
        )
        self.executor = executor or ToolExecutor(self.registry)
        self.prompt_loader = prompt_loader or PromptLoader()
        self.session_store = SessionStore(self.config.session_dir)
        self.session_store.cleanup(
            ttl_hours=self.config.session_ttl_hours,
            max_count=self.config.session_max_count,
        )
        self.skill_catalog = SkillCatalog(
            self.config.tool_library_dir,
            embedding_fn=create_embedding_fn(self.config),
        )
        _executor_agent = ExecutorAgent(
            ExecutorDependencies(
                run_tool=self.run_tool,
                handle_success=self._handle_successful_tool_result,
                plan_correction=self._plan_correction_with_llm,
                max_self_corrections=self.config.max_self_corrections,
            )
        )
        self.router = StateMachineRouter(
            RouterDependencies(
                create_state=self._create_state,
                plan_with_llm=self._plan_with_state,
                executor_agent=_executor_agent,
                critique_execution=self._critique_execution_with_llm,
                retrieve_skills=self._retrieve_skills,
                code_with_llm=self._code_with_llm,
                skill_catalog=self.skill_catalog,
                make_response=AgentResponse,
                max_steps=self.config.max_router_steps,
            )
        )

    def list_tools(self) -> list:
        """Return currently registered tools."""

        return self.registry.list()

    def run(self, task: str) -> AgentResponse:
        """Run one preserved user task through the state-machine router."""
        return self.router.run(task)

    def run_tool(self, tool_name: str, arguments: dict[str, Any], *, _state: AgentState | None = None):
        """Execute an explicitly named tool without natural-language planning.

        Generated-tool usage stats are recorded once at this single entry
        point so that the manifest's ``usage_count``/``failure_count`` reflect
        every invocation — both direct ``--tool`` CLI calls and router-driven
        executions. The optional ``_state`` is internal: when the router
        path passes its :class:`AgentState`, this method also emits the
        ``generated_tool_usage_recorded`` event for observability.
        """

        result = self.executor.run(tool_name, arguments)
        self._maybe_record_generated_tool_usage(tool_name, result.success, _state)
        return result

    def _maybe_record_generated_tool_usage(
        self,
        tool_name: str,
        success: bool,
        state: AgentState | None = None,
    ) -> None:
        """Forward usage stats to the librarian for generated tools only."""

        tool = self.registry.get(tool_name)
        if tool is None or tool.source != "generated":
            return
        updated = self.router.librarian_agent.record_usage(tool_name, success=success)
        if updated is not None and state is not None:
            state.record_event(
                "generated_tool_usage_recorded",
                tool_name=tool_name,
                success=success,
                usage_count=updated.get("usage_count"),
                failure_count=updated.get("failure_count"),
            )

    def resume(self, session_id: str, *, user_input: str | None = None, approve: bool = False, reject: bool = False) -> AgentResponse:
        """Resume a pending HITL session with explicit user input or decision."""

        snapshot = self.session_store.load_pending(session_id)
        if reject:
            self.session_store.close(session_id, "rejected")
            return AgentResponse(
                task=str(snapshot.get("user_task") or ""),
                output="세션 요청을 거부했습니다.",
                action="rejected",
                session_id=session_id,
            )
        state = self._create_state()
        state.session_id = session_id
        state.user_task = str(snapshot.get("user_task") or "")
        state.current_plan = snapshot.get("current_plan") if isinstance(snapshot.get("current_plan"), dict) else {}
        state.last_tool_name = str(snapshot.get("last_tool_name") or "") or None
        state.last_tool_arguments = (
            snapshot.get("last_tool_arguments") if isinstance(snapshot.get("last_tool_arguments"), dict) else {}
        )
        state.last_tool_result = (
            snapshot.get("last_tool_result") if isinstance(snapshot.get("last_tool_result"), dict) else None
        )
        state.reflections = snapshot.get("reflections") if isinstance(snapshot.get("reflections"), list) else []
        if approve and isinstance(snapshot.get("resume_plan"), dict) and snapshot["resume_plan"].get("action") == "tool":
            state.current_plan = dict(snapshot["resume_plan"])
            state.next_node = "execute"
        else:
            resumed_text = user_input if user_input is not None else ("approved" if approve else "")
            if resumed_text:
                state.append_message("user", resumed_text)
                state.user_task = f"{state.user_task}\n\nAdditional user input: {resumed_text}"
            state.next_node = "retrieve"
        state.record_event("session_resumed", session_id=session_id, approved=approve, has_input=user_input is not None)
        response = self.router.run_state(state)
        approval_resume = approve and isinstance(snapshot.get("resume_plan"), dict) and snapshot["resume_plan"].get("action") == "tool"
        if self._resume_completed_successfully(response) and (not approval_resume or response.action == "tool"):
            self.session_store.close(session_id, "completed")
        return response

    def _handle_successful_tool_result(
        self,
        task: str,
        tool_name: str,
        output: Any,
        state: AgentState,
    ) -> AgentResponse | None:
        """Apply follow-up routing for successful tool executions."""

        if tool_name == "tool_create" and isinstance(output, dict):
            tool_name_from_output = str(output.get("name") or "")
            state.current_plan = {
                "action": "tool",
                "tool_name": "tool_validate",
                "arguments": {"name": tool_name_from_output},
            }
            state.next_node = "execute"
            state.record_event("generated_tool_created", tool_name=tool_name_from_output)
            return None
        if tool_name == "tool_validate" and isinstance(output, dict):
            generated_tool = output.get("tool")
            generated_name = ""
            if isinstance(generated_tool, dict):
                generated_name = str(generated_tool.get("name") or "")
            approval_output = {
                "status": "approval_required",
                "plan": {"action": "approve_tool", "name": generated_name},
                "risk_level": "high",
                "approved": False,
            }
            state.next_node = "approve"
            pending = self._save_pending_session(
                state,
                approval_output,
                resume_plan={"action": "tool", "tool_name": "tool_approve", "arguments": {"name": generated_name}},
            )
            state.record_event("generated_tool_validation_pending_approval", tool_name=generated_name)
            state.record_event("final_response_created", action="approval_required")
            return AgentResponse(
                task=task,
                output=approval_output,
                tool_name=tool_name,
                action="approval_required",
                events=state.events,
                session_id=state.session_id,
                pending=pending,
            )
        if tool_name in {"ask_human", "propose_actions"}:
            state.next_node = "approve"
            pending = self._save_pending_session(state, output)
            state.record_event("final_response_created", action="tool")
            return AgentResponse(
                task=task,
                output=output,
                tool_name=tool_name,
                action="tool",
                events=state.events,
                session_id=state.session_id,
                pending=pending,
            )
        state.next_node = "critique"
        return None

    def _resume_completed_successfully(self, response: AgentResponse) -> bool:
        action = str(getattr(response, "action", ""))
        pending = getattr(response, "pending", None)
        if isinstance(pending, dict) and pending.get("status") == "pending":
            return False
        return action not in {"approval_required", "tool_error", "llm_error", "router_error", "critic_error", "error"}

    def _save_pending_session(
        self,
        state: AgentState,
        output: Any,
        *,
        resume_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pending = self.session_store.save_pending(state, output, resume_plan=resume_plan)
        state.pending = pending
        state.session_status = "pending"
        state.record_event(
            "session_pending_saved",
            session_id=state.session_id,
            pending_type=pending.get("pending_type"),
            cleanup_hint=f"rm {self.config.session_dir / (state.session_id + '.json')}",
        )
        return pending

    def _plan_with_llm(self, task: str) -> dict[str, Any]:
        """Create a normalized plan from the LLM response."""

        response = self.llm_client.complete(self._build_prompt(task))
        parsed = self._loads_plan_json(response)

        return self._normalize_plan(parsed, fallback_response=response)

    def _plan_with_state(self, state: AgentState) -> dict[str, Any]:
        """Create a normalized plan with shared-state context."""

        response = self.llm_client.complete(self._build_prompt(state.user_task, state=state))
        parsed = self._loads_plan_json(response)

        return self._normalize_plan(parsed, fallback_response=response)

    def _retrieve_skills(self, state: AgentState) -> list[dict[str, Any]]:
        """Retrieve reusable skill hints for planning."""

        search_tool = self.registry.get("tool_search")
        if search_tool is None:
            return []
        result = search_tool.handler({"query": state.user_task, "top_k": 5})
        if not result.success or not isinstance(result.output, dict):
            return []
        matches = result.output.get("matches")
        return matches if isinstance(matches, list) else []

    def _code_with_llm(self, state: AgentState) -> dict[str, Any]:
        """Create generated-tool code from the Coder Agent prompt."""

        response = self.llm_client.complete(
            self.prompt_loader.render(
                "coder.txt",
                plan=json.dumps(state.current_plan, ensure_ascii=False, default=str),
                task=state.user_task,
                observations=json.dumps(
                    {
                        "last_tool_result": state.last_tool_result,
                        "error_log": state.error_log,
                        "reflections": state.reflections,
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            )
        )
        try:
            parsed = self._loads_plan_json(response)
        except json.JSONDecodeError:
            parsed = response
        if not isinstance(parsed, dict):
            return {"code": str(response)}
        return parsed

    def _plan_correction_with_llm(
        self,
        task: str,
        failed_plan: dict[str, Any],
        *,
        error: str | None,
        output: Any,
    ) -> dict[str, Any]:
        """Create a correction plan from failed tool execution observations."""

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

    def _critique_execution_with_llm(self, state: AgentState) -> dict[str, Any]:
        """Create a normalized critique verdict from the latest execution state."""

        response = self.llm_client.complete(self._build_critic_prompt(state))
        try:
            parsed = self._loads_plan_json(response)
        except json.JSONDecodeError:
            parsed = response
        critique = self._normalize_critique(parsed, fallback_response=response)
        # If the tool succeeded AND the Critic has already issued at least one verdict on
        # this execution, block a second retry to prevent infinite critique loops.
        prior_critiques = sum(1 for e in state.events if e.name == "execution_critiqued")
        if (
            state.last_tool_result
            and state.last_tool_result.get("success")
            and prior_critiques >= 1
            and critique.get("verdict") == "retry"
        ):
            critique["verdict"] = "success"
            critique["next_node"] = "done"
        return critique

    def _normalize_json_control_chars(self, text: str) -> str:
        """Normalize LLM-generated JSON with invalid escape sequences or bare control chars.

        Handles two classes of RFC 8259 violations commonly produced by LLMs:
        1. Bare control characters (newlines, tabs, etc.) inside string values.
        2. Non-standard escape sequences (e.g. \\' for apostrophe) that are valid
           in Python/JavaScript but are undefined in JSON.

        For case 2: if a backslash is followed by a character not in the set of
        valid JSON escapes (", \\, /, b, f, n, r, t, u), the backslash is dropped
        and the character is emitted as-is, because most such characters do not
        need escaping inside JSON strings.
        """
        _VALID_JSON_AFTER_BACKSLASH = frozenset('"\\' + "/bfnrtu")
        _CTRL_MAP = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}

        result: list[str] = []
        in_string = False
        pending_backslash = False

        for ch in text:
            if pending_backslash:
                pending_backslash = False
                if ch in _VALID_JSON_AFTER_BACKSLASH:
                    result.append("\\")
                    result.append(ch)
                else:
                    # Invalid JSON escape (e.g. \' \a \x): drop backslash, keep char.
                    result.append(ch)
            elif in_string and ch == "\\":
                pending_backslash = True
            elif ch == '"':
                result.append(ch)
                in_string = not in_string
            elif in_string and ch in _CTRL_MAP:
                result.append(_CTRL_MAP[ch])
            elif in_string and ord(ch) < 0x20:
                result.append(f"\\u{ord(ch):04x}")
            else:
                result.append(ch)

        if pending_backslash:
            result.append("\\")

        return "".join(result)

    def _loads_plan_json(self, response: str) -> object:
        """Decode nested or fenced LLM plan JSON into a Python object."""

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
                pass
            # Some LLMs emit literal newlines/control chars inside JSON strings.
            try:
                parsed = json.loads(self._normalize_json_control_chars(stripped))
                continue
            except json.JSONDecodeError:
                pass
            extracted = self._extract_json_object(stripped)
            if extracted == stripped:
                repaired = self._repair_truncated_json(stripped)
                if repaired is not None:
                    return repaired
                return parsed
            try:
                parsed = json.loads(extracted)
                continue
            except json.JSONDecodeError:
                pass
            try:
                parsed = json.loads(self._normalize_json_control_chars(extracted))
                continue
            except json.JSONDecodeError:
                pass
            repaired = self._repair_truncated_json(extracted)
            if repaired is not None:
                return repaired
            # extracted may have been mis-cut (rfind catches a } inside a string
            # value).  Fall back to repairing the original uncut text.
            if extracted != stripped:
                repaired = self._repair_truncated_json(stripped)
                if repaired is not None:
                    return repaired
            return parsed
        return parsed

    def _repair_truncated_json(self, text: str) -> object | None:
        """Try to fix a JSON object truncated before its closing braces or string delimiters.

        LLMs that hit token limits may produce JSON where the innermost string value
        (e.g. a code field) is not closed.  We try successively longer suffixes that
        close open strings and then the enclosing objects.
        """

        if not text.strip().startswith("{"):
            return None
        for extra in ("}", "}}", "}}}", '"}}', '"}}}', '")}', '")}}"'):
            try:
                return json.loads(text + extra)
            except json.JSONDecodeError:
                continue
        return None

    def _extract_json_object(self, text: str) -> str:
        """Extract the outermost JSON object candidate from free-form text."""

        text = self._strip_markdown_code_fence(text)
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return text
        return text[start : end + 1]

    def _strip_markdown_code_fence(self, text: str) -> str:
        """Remove a surrounding Markdown code fence from model output."""

        stripped = text.strip()
        if not stripped.startswith("```"):
            return text
        lines = stripped.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```"):
            if lines[-1].strip() == "```":
                return "\n".join(lines[1:-1]).strip()
            return "\n".join(lines[1:]).strip()
        return text

    def _normalize_plan(self, parsed: object, *, fallback_response: str) -> dict[str, Any]:
        """Normalize model output to the executable agent plan contract."""

        if not isinstance(parsed, dict):
            if isinstance(parsed, str) and self._looks_like_clarification_response(parsed):
                return self._ask_human_plan(parsed)
            return {
                "action": "respond",
                "response": fallback_response,
                "_validation_error": "plan_not_object",
            }

        # Multi-action parallel plan: {"actions": [...]} or {"action": "parallel", "actions": [...]}
        raw_actions = parsed.get("actions")
        if isinstance(raw_actions, list) and raw_actions:
            sub_plans = [
                self._normalize_plan(a, fallback_response=fallback_response)
                for a in raw_actions
                if isinstance(a, dict)
            ]
            tool_plans = [p for p in sub_plans if p.get("action") == "tool"]
            if tool_plans:
                return {"action": "parallel", "actions": tool_plans}

        raw_action = parsed.get("action")
        if isinstance(raw_action, str) and self._is_clarification_action(raw_action):
            response = self._clarification_text(parsed, fallback_response)
            return self._ask_human_plan(response)

        if isinstance(raw_action, str):
            parsed = self._normalize_plan_action_alias(parsed)
            raw_action = parsed.get("action")

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
            if self._looks_like_clarification_response(response):
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
            # LLMs occasionally wrap the arguments object in an array.
            # Unwrap single-element list: [{"key": "val"}] → {"key": "val"}
            # Unwrap list of [key, val] pairs:  [["key","val"]] → {"key":"val"}
            if isinstance(arguments, list):
                if len(arguments) == 1 and isinstance(arguments[0], dict):
                    arguments = arguments[0]
                elif arguments and all(
                    isinstance(item, (list, tuple)) and len(item) == 2
                    for item in arguments
                ):
                    arguments = dict(arguments)
            if not isinstance(arguments, dict):
                return {
                    "action": "respond",
                    "response": "툴 실행 인자가 객체가 아니어서 실행하지 않았습니다.",
                    "_validation_error": "invalid_tool_arguments",
                }
        arguments = self._normalize_tool_arguments(tool_name, arguments, parsed)
        return {"action": "tool", "tool_name": tool_name, "arguments": arguments}

    def _normalize_critique(self, parsed: object, *, fallback_response: str) -> dict[str, Any]:
        """Normalize critic output to verdict, reason, reflection, and next_node."""

        if not isinstance(parsed, dict):
            return {
                "verdict": "success",
                "reason": "critic returned non-JSON output after successful execution",
                "reflection": str(fallback_response),
                "next_node": "done",
                "_validation_error": "critic_not_object",
            }

        raw_verdict = str(parsed.get("verdict") or "").strip().lower().replace("-", "_")
        verdict_aliases = {
            "accepted": "success",
            "pass": "success",
            "passed": "success",
            "retryable": "retry",
            "retry_needed": "retry",
            "human": "needs_human",
            "input_required": "needs_human",
        }
        verdict = verdict_aliases.get(raw_verdict, raw_verdict)
        if verdict not in {"success", "retry", "needs_human", "unsafe", "failed"}:
            verdict = "success"

        _verdict_to_node = {
            "success": "done",
            "retry": "plan",
            "needs_human": "approve",
            "unsafe": "error",
            "failed": "error",
        }
        next_node = _verdict_to_node[verdict]
        return {
            "verdict": verdict,
            "reason": str(parsed.get("reason") or ""),
            "reflection": str(parsed.get("reflection") or ""),
            "next_node": next_node,
        }

    def _normalize_plan_action_alias(self, parsed: dict[str, Any]) -> dict[str, Any]:
        """Map node-level plan actions to the executable plan contract."""

        raw_action = parsed.get("action")
        if not isinstance(raw_action, str):
            return parsed
        normalized_action = raw_action.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized_action == "use_tool":
            return {
                **parsed,
                "action": "tool",
                "tool_name": parsed.get("tool_name") or parsed.get("tool") or parsed.get("name"),
            }
        if normalized_action == "create_tool":
            return {
                **parsed,
                "action": "tool",
                "tool_name": "tool_create",
                "arguments": self._arguments_with_top_level_fields(parsed, "name", "description", "code"),
            }
        if normalized_action in {"approve_tool", "store_tool"}:
            return {
                **parsed,
                "action": "tool",
                "tool_name": "tool_approve",
                "arguments": self._arguments_with_top_level_fields(parsed, "name"),
            }
        if normalized_action == "final_answer":
            return {
                **parsed,
                "action": "respond",
                "response": str(parsed.get("response") or parsed.get("answer") or ""),
            }
        return parsed

    def _arguments_with_top_level_fields(self, parsed: dict[str, Any], *fields: str) -> dict[str, Any]:
        """Promote selected top-level plan fields into tool arguments."""

        raw_arguments = parsed.get("arguments")
        arguments = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
        for field in fields:
            if field not in arguments and field in parsed:
                arguments[field] = parsed[field]
        return arguments

    def _normalize_tool_arguments(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        parsed: dict[str, Any],
    ) -> dict[str, Any]:
        """Normalize common model argument aliases to builtin tool schemas."""

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
        """Inline stdin-style aliases for the current code_execute contract."""

        replacement = f"({stdin_value!r})"
        return code.replace("sys.stdin.read()", replacement).replace("input()", replacement)

    def _is_clarification_action(self, raw_action: str) -> bool:
        normalized = raw_action.strip().lower().replace("-", "_").replace(" ", "_")
        return normalized in _CLARIFICATION_ACTIONS or any(
            token in normalized for token in ("ask", "clarif", "input")
        )

    def _looks_like_clarification_response(self, response: str) -> bool:
        """Detect direct clarification text that should route to HITL."""

        normalized = response.casefold()
        clarification_markers = (
            "please provide",
            "provide more",
            "provide more details",
            "more details",
            "more detail",
            "need access",
            "need credentials",
            "missing",
            "which data",
            "what data",
            "clarify",
            "추가 정보",
            "알려주세요",
            "제공",
        )
        return any(marker in normalized for marker in clarification_markers)

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

    def _build_prompt(self, task: str, *, state: AgentState | None = None) -> str:
        """Render the Plan Agent prompt with available tools and task text."""

        tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "category": tool.category,
                "usage": tool.usage,
            }
            for tool in self.registry.list()
        ]
        return self.prompt_loader.render(
            "plan.txt",
            available_tools=json.dumps(tools, ensure_ascii=False),
            task=task,
            retrieved_skills=json.dumps(state.retrieved_skills if state else [], ensure_ascii=False, default=str),
            reflections=json.dumps(state.reflections if state else [], ensure_ascii=False, default=str),
            last_tool_result=json.dumps(state.last_tool_result if state else None, ensure_ascii=False, default=str),
        )

    def _build_correction_prompt(
        self,
        task: str,
        failed_plan: dict[str, Any],
        *,
        error: str | None,
        output: Any,
    ) -> str:
        """Render the correction prompt for failed tool execution."""

        return self.prompt_loader.render(
            "correction.txt",
            task=task,
            failed_plan=json.dumps(failed_plan, ensure_ascii=False),
            error=error,
            output=json.dumps(output, ensure_ascii=False, default=str),
        )

    def _build_critic_prompt(self, state: AgentState) -> str:
        """Render the Critic Agent prompt from the latest execution state."""

        return self.prompt_loader.render(
            "critic.txt",
            task=state.user_task,
            current_plan=json.dumps(state.current_plan, ensure_ascii=False, default=str),
            last_tool_result=json.dumps(state.last_tool_result, ensure_ascii=False, default=str),
            error_log=state.error_log,
            reflections=json.dumps(state.reflections, ensure_ascii=False, default=str),
        )

    def _create_state(self) -> AgentState:
        """Create initial AgentState from the current tool registry."""

        return AgentState(
            available_tools=[
                ToolSchema(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.parameters or {},
                    returns=tool.returns or {},
                    safety_level=tool.safety_level,
                    source=tool.source,
                    validation_status=tool.validation_status,
                )
                for tool in self.registry.list()
            ]
        )
