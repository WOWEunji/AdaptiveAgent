"""Adaptive agent orchestration."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from adaptive_agent.agents.executor import ExecutorAgent, ExecutorDependencies
from adaptive_agent.config import AgentConfig
from adaptive_agent.conversation import ConversationSession, PendingSave
from adaptive_agent.llms.base import LLMClient
from adaptive_agent.llms.factory import create_coder_llm_client, create_embedding_fn, create_llm_client
from adaptive_agent.prompts import PromptLoader
from adaptive_agent.response import AgentResponse
from adaptive_agent.router import RouterDependencies, StateMachineRouter
from adaptive_agent.skills import SkillCatalog
from adaptive_agent.state import AgentState, ToolSchema
from adaptive_agent.tools.executor import ToolExecutor
from adaptive_agent.tools.registry import ToolRegistry, create_default_registry

if TYPE_CHECKING:
    from adaptive_agent.logging import AgentLogger


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
        logger: AgentLogger | None = None,
    ) -> None:
        self.config = config or AgentConfig.from_env()
        self.llm_client = llm_client or create_llm_client(self.config)
        self.coder_llm_client: LLMClient = create_coder_llm_client(self.config) or self.llm_client
        self.registry = registry or create_default_registry(
            self.config.workspace_dir,
            tool_library_dir=self.config.tool_library_dir,
            artifact_dir=self.config.artifact_dir,
        )
        self.executor = executor or ToolExecutor(self.registry)
        self.prompt_loader = prompt_loader or PromptLoader()
        self.skill_catalog = SkillCatalog(
            self.config.tool_library_dir,
            embedding_fn=create_embedding_fn(self.config),
        )
        self.current_session: ConversationSession | None = None
        _executor_agent = ExecutorAgent(
            ExecutorDependencies(
                run_tool=self.run_tool,
                handle_success=self._handle_successful_tool_result,
                plan_correction=self._plan_correction_with_llm,
                max_self_corrections=self.config.max_self_corrections,
                logger=logger,
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
                synthesize_result=self._synthesize_result,
                synthesize_code_save=self._synthesize_code_save,
                skill_catalog=self.skill_catalog,
                make_response=AgentResponse,
                max_steps=self.config.max_router_steps,
                logger=logger,
            )
        )

    def list_tools(self) -> list:
        """Return currently registered tools."""

        return self.registry.list()

    def run(self, task: str) -> AgentResponse:
        """Run one preserved user task through the state-machine router."""
        return self.router.run(task)

    def run_turn(self, task: str, session: ConversationSession) -> AgentResponse:
        """Run one turn within a multi-turn conversation session."""
        self.current_session = session
        try:
            if session.pending_action:
                return self._handle_pending_action(task, session)
            return self._run_turn_normal(task, session)
        finally:
            self.current_session = None

    def _run_turn_normal(self, task: str, session: ConversationSession) -> AgentResponse:
        """Execute the state-machine router for a fresh turn."""
        from adaptive_agent.state import Message  # noqa: F401 — used via state
        state = self._create_state()
        state.history = list(session.history)
        state.user_task = task
        state.next_node = "retrieve"
        state.record_event("task_received", task=task)
        state.append_message("user", task)
        response = self.router.run_state(state)
        self._update_session_after_turn(session, state, response)
        return response

    def _handle_pending_action(self, user_input: str, session: ConversationSession) -> AgentResponse:
        """Resolve a pending approval/confirmation with the user's answer."""
        from adaptive_agent.state import Message

        action = session.pending_action
        session.pending_action = None

        session.history.append(Message(role="user", content=user_input))
        import re as _re
        _YES = _re.compile(r'\b(yes|y|ok|승인|저장)\b|^(네|예)$', _re.IGNORECASE)
        is_yes = bool(_YES.search(user_input.strip()))

        if action.get("type") == "code_save":
            if is_yes:
                self.save_code(action["name"], action["description"], action["code"])
                msg = f"'{action['name']}' 스킬이 저장되었습니다."
            else:
                msg = "저장을 취소했습니다."
            session.history.append(Message(role="assistant", content=msg))
            return AgentResponse(task=action.get("task", ""), output=msg, action="tool", summary=msg)

        if action.get("type") == "tool_approve":
            if is_yes:
                result = self.run_tool("tool_approve", {"name": action["name"]})
                msg = (
                    f"'{action['name']}' 스킬이 저장되었습니다."
                    if result.success
                    else f"저장 실패: {result.error}"
                )
            else:
                msg = "승인을 취소했습니다."
            session.history.append(Message(role="assistant", content=msg))
            return AgentResponse(task=action.get("task", ""), output=msg, action="tool", summary=msg)

        # 알 수 없는 pending_action — 일반 턴으로 처리
        return self._run_turn_normal(user_input, session)

    def _update_session_after_turn(
        self,
        session: ConversationSession,
        state: AgentState,
        response: AgentResponse,
    ) -> None:
        """Append new messages from this turn to the session history."""
        from adaptive_agent.state import Message
        prior_len = len(session.history)
        for msg in state.history[prior_len:]:
            session.history.append(msg)
        # ask_human pending: 질문과 상태를 history에 기록해 다음 턴에서 이미 확인했음을 알 수 있게 함
        if isinstance(response.output, dict) and response.output.get("status") == "pending_human_input":
            questions = response.output.get("questions") or []
            q_text = questions[0] if questions else "확인 요청"
            session.history.append(Message(role="assistant", content=f"[확인 요청] {str(q_text)[:200]}"))
        else:
            summary = response.summary or ""
            if not summary and isinstance(response.output, str) and response.output.strip():
                # respond 액션: LLM이 텍스트로 답한 경우 — history에 기록해야 다음 턴이 맥락을 봄
                summary = response.output.strip()[:400]
            if not summary and isinstance(response.output, dict):
                execution = (response.output.get("execution") or {})
                summary = str(execution.get("stdout", ""))[:200].strip()
            if summary:
                session.history.append(Message(role="assistant", content=summary))

    def save_session_codes(
        self,
        session: ConversationSession,
        approve_fn: "Callable[[PendingSave], bool]",
    ) -> None:
        """Process pending saves collected during the session.

        approve_fn: called for each PendingSave; returns True to save, False to skip.
        """
        import hashlib
        import re as _re

        _SAFE_NAME = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]{1,63}$")

        for item in session.pending_saves:
            if not approve_fn(item):
                continue
            code = item.code or ""
            if not code.strip():
                continue
            try:
                import ast as _ast
                tree = _ast.parse(code)
                already_wrapped = any(
                    isinstance(n, _ast.FunctionDef) and n.name == "run"
                    for n in _ast.walk(tree)
                )
            except SyntaxError:
                already_wrapped = False

            wrapped = code if already_wrapped else (
                "def run(arguments):\n"
                + "\n".join(f"    {ln}" for ln in code.splitlines())
                + "\n"
            )

            name = item.suggested_name
            if not _SAFE_NAME.match(name):
                name = _re.sub(r"[^A-Za-z0-9_]", "_", name)[:40].lstrip("0123456789_") or f"tool_{item.turn_session_id[:8]}"

            self.config.tool_library_dir.mkdir(parents=True, exist_ok=True)
            py_path = self.config.tool_library_dir / f"{name}.py"
            py_path.write_text(wrapped, encoding="utf-8")
            file_hash = hashlib.sha256(wrapped.encode()).hexdigest()
            self.skill_catalog.upsert({
                "name": name,
                "description": item.suggested_desc,
                "file_path": str(py_path),
                "file_hash": file_hash,
                "validation_status": "executed",
                "approval_status": "approved",
                "approved": True,
                "category": "generated",
                "tags": [],
            })

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
            validate_arguments: dict[str, Any] = {"name": tool_name_from_output}
            # Forward sample_arguments from the coder so tool_validate can run a real sample
            # call instead of run({}), which raises ValueError/KeyError for tools that require input.
            sample_args = (state.current_plan.get("arguments") or {}).get("sample_arguments")
            if isinstance(sample_args, dict) and sample_args:
                validate_arguments["sample_arguments"] = sample_args
            state.current_plan = {
                "action": "tool",
                "tool_name": "tool_validate",
                "arguments": validate_arguments,
            }
            state.next_node = "execute"
            state.record_event("generated_tool_created", tool_name=tool_name_from_output)
            return None
        if tool_name == "tool_validate" and isinstance(output, dict):
            generated_tool = output.get("tool")
            generated_name = ""
            if isinstance(generated_tool, dict):
                generated_name = str(generated_tool.get("name") or "")
            state.current_plan = {
                "action": "tool",
                "tool_name": "tool_approve",
                "arguments": {"name": generated_name},
            }
            state.next_node = "execute"
            state.record_event("generated_tool_validation_auto_approve", tool_name=generated_name)
            return None
        if tool_name == "tool_approve" and isinstance(output, dict):
            tool_meta = output.get("tool") or {}
            approved_name = str(tool_meta.get("name") or output.get("name") or "")
            msg = f"'{approved_name}' 스킬이 라이브러리에 저장되었습니다." if approved_name else "스킬이 저장되었습니다."
            state.next_node = "done"
            state.summary = msg
            state.record_event("final_response_created", action="tool")
            return AgentResponse(
                task=task,
                output=msg,
                tool_name=tool_name,
                action="tool",
                events=state.events,
                summary=msg,
            )
        if tool_name in {"ask_human", "propose_actions"}:
            questions = (output or {}).get("questions") or [] if isinstance(output, dict) else []
            prompt = (questions[0] if questions else "추가 정보를 입력해 주세요.")[:200]
            state.next_node = "approve"
            state.record_event("final_response_created", action="ask_human")
            return AgentResponse(
                task=task,
                output=output,
                tool_name=tool_name,
                action="ask_human",
                events=state.events,
                needs_input=True,
                input_prompt=prompt,
            )
        state.next_node = "critique"
        return None

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
        if not isinstance(matches, list):
            return []
        # registry에 로드 실패한 스킬은 제외 — catalog에만 있고 실행 불가한 스킬을 플래너가 선택하면 "Unknown tool" 오류 발생
        failed_names = {
            str(r.get("name"))
            for r in self.registry.generated_load_results
            if not r.get("loaded")
        }
        return [m for m in matches if str(m.get("name", "")) not in failed_names]

    def _code_with_llm(self, state: AgentState) -> dict[str, Any]:
        """Create generated-tool code from the Coder Agent prompt."""

        response = self.coder_llm_client.complete(
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

    def _synthesize_result(self, state: AgentState) -> str:
        """Generate a natural language answer from the tool execution result."""
        tool_result = state.last_tool_result or {}
        raw_output = tool_result.get("output")
        if isinstance(raw_output, dict):
            execution = raw_output.get("execution") or {}
            stdout = execution.get("stdout", "") if isinstance(execution, dict) else ""
        elif isinstance(raw_output, str):
            # 파일 목록·텍스트 결과 등 직접 string 반환 툴 — stdout으로 올려서 LLM이 볼 수 있게 함
            stdout = raw_output
        else:
            stdout = ""

        try:
            tool_result_preview = json.dumps(raw_output, ensure_ascii=False, default=str)[:1500]
        except Exception:
            tool_result_preview = str(raw_output)[:1500]

        return self.llm_client.complete(
            self.prompt_loader.render(
                "synthesize.txt",
                task=state.user_task,
                tool_name=state.last_tool_name or "",
                stdout=stdout.strip(),
                tool_result=tool_result_preview,
                generated_code=(state.generated_code or "").strip(),
                language=self.config.language,
            )
        )

    def _suggest_skill_name(self, task: str, code: str) -> str:
        """LLM에게 스킬 이름 제안을 요청한다. 실패 시 정규식으로 폴백."""
        import re as _re
        _SAFE = _re.compile(r'^[a-z][a-z0-9_]{1,39}$')
        prompt = (
            "Generate a concise Python snake_case function name (2-4 words, max 30 chars, lowercase) "
            "that describes this task. Output ONLY the name, nothing else.\n\n"
            f"Task: {task[:200]}\n"
            f"Code summary: {code.splitlines()[0][:100] if code else ''}"
        )
        try:
            raw = self.llm_client.complete(prompt).strip().split()[0].lower()
            raw = _re.sub(r'[^a-z0-9_]', '_', raw)[:40].strip('_')
            if _SAFE.match(raw):
                return raw
        except Exception:
            pass
        # 폴백: 기존 정규식 로직
        first = task.split('\n')[0][:40]
        words = _re.sub(r'[^A-Za-z0-9\s]', '', first).split()
        raw = '_'.join(w.lower() for w in words[:3])[:30] if words else ""
        if raw and _SAFE.match(raw):
            return raw
        return f"tool_{__import__('uuid').uuid4().hex[:8]}"

    def _synthesize_code_save(self, state: AgentState) -> AgentResponse:
        """code_execute 성공 후 완료 응답을 반환한다. 저장 여부는 LLM 응답에 포함."""
        state.record_event("final_response_created", action="tool")
        return AgentResponse(
            task=state.user_task,
            output=(state.last_tool_result or {}).get("output"),
            tool_name="code_execute",
            action="tool",
            events=state.events,
            summary=state.summary,
        )

    def save_code(self, name: str, description: str, code: str) -> None:
        """검증 없이 code를 스킬로 직접 저장한다. approval_required 응답 처리 후 호출."""
        import hashlib, re as _re
        _SAFE_NAME = _re.compile(r'^[A-Za-z_][A-Za-z0-9_]{1,63}$')
        if not _SAFE_NAME.match(name):
            name = _re.sub(r'[^A-Za-z0-9_]', '_', name)[:40].lstrip('0123456789_') or f"tool_{__import__('uuid').uuid4().hex[:8]}"

        already_wrapped = False
        try:
            import ast as _ast
            already_wrapped = any(
                isinstance(n, _ast.FunctionDef) and n.name == "run"
                for n in _ast.walk(_ast.parse(code))
            )
        except SyntaxError:
            pass

        wrapped = code if already_wrapped else (
            "def run(arguments):\n" + "\n".join(f"    {ln}" for ln in code.splitlines()) + "\n"
        )
        self.config.tool_library_dir.mkdir(parents=True, exist_ok=True)
        py_path = self.config.tool_library_dir / f"{name}.py"
        py_path.write_text(wrapped, encoding="utf-8")
        file_hash = hashlib.sha256(wrapped.encode()).hexdigest()
        self.skill_catalog.upsert({
            "name": name, "description": description,
            "file_path": str(py_path), "file_hash": file_hash,
            "validation_status": "executed", "approval_status": "approved",
            "approved": True, "category": "generated", "tags": [],
        })

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
        # If the tool succeeded, always block retry regardless of how many critiques have run.
        # The prior_critiques >= 1 guard only caught second-and-later critiques; the first
        # critique on a successful execution could still issue retry and trigger an extra
        # plan→execute→critique cycle.
        if (
            state.last_tool_result
            and state.last_tool_result.get("success")
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
        (e.g. a code or description field) is not closed.  We count unclosed braces
        to build an appropriate suffix, then try a fixed set of fallbacks.
        """

        stripped = text.strip()
        if not stripped.startswith("{"):
            return None

        # Count unclosed braces outside of string literals to build a targeted suffix.
        depth = 0
        in_string = False
        escaped = False
        for ch in stripped:
            if escaped:
                escaped = False
                continue
            if ch == "\\" and in_string:
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1

        # Build closing suffix from depth count (capped to avoid runaway).
        if 1 <= depth <= 6:
            dynamic_suffix = "}" * depth
            try:
                return json.loads(stripped + dynamic_suffix)
            except json.JSONDecodeError:
                pass
            # Unclosed string before the braces.
            try:
                return json.loads(stripped + '"' + dynamic_suffix)
            except json.JSONDecodeError:
                pass

        # Fixed fallbacks cover edge cases the depth counter misses (e.g. mid-string truncation).
        for extra in ("}", "}}", "}}}", '"}}', '"}}}', '"}}}}', '")}', '")}}"', '"}}}}}"'):
            try:
                return json.loads(stripped + extra)
            except json.JSONDecodeError:
                continue
        return None

    def _extract_json_object(self, text: str) -> str:
        """Extract the outermost JSON object from free-form text using balanced-bracket tracking.

        Uses state-aware bracket counting that skips { and } inside string literals,
        so code fields containing ``return {}`` or similar do not cause early truncation.
        """

        text = self._strip_markdown_code_fence(text)
        start = text.find("{")
        if start == -1:
            return text

        depth = 0
        in_string = False
        escaped = False
        for i, ch in enumerate(text[start:], start):
            if escaped:
                escaped = False
                continue
            if ch == "\\" and in_string:
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start : i + 1]

        # No balanced close found — return from start to end of text.
        return text[start:]

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
        plan: dict[str, Any] = {"action": "tool", "tool_name": tool_name, "arguments": arguments}
        if reasoning := parsed.get("reasoning"):
            plan["reasoning"] = str(reasoning)
        return plan

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

        # Only name + description to keep the tool list compact and avoid token pressure
        # that causes plan JSON truncation on smaller models.
        tools = [
            {"name": tool.name, "description": tool.description}
            for tool in self.registry.list()
        ]
        # Build conversation history snippet from the most recent turns (up to 10 messages)
        history_text = ""
        if state and state.history:
            recent = state.history[-10:] if len(state.history) > 10 else state.history
            lines = [
                f"{m.role}: {m.content[:600]}"
                for m in recent
                if m.role in ("user", "assistant")
            ]
            if lines:
                history_text = "Recent conversation:\n" + "\n".join(lines) + "\n---\n"

        return self.prompt_loader.render(
            "plan.txt",
            available_tools=json.dumps(tools, ensure_ascii=False),
            task=task,
            retrieved_skills=json.dumps(state.retrieved_skills if state else [], ensure_ascii=False, default=str),
            reflections=json.dumps(state.reflections if state else [], ensure_ascii=False, default=str),
            last_tool_result=json.dumps(state.last_tool_result if state else None, ensure_ascii=False, default=str),
            conversation_history=history_text,
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
