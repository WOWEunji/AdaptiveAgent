"""Coder role agent contract."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from adaptive_agent.agents.base import AgentResult, BaseRoleAgent
from adaptive_agent.state import AgentState

_TOOL_CREATE_REQUIRED = ("name", "description", "code")
_CODE_EXECUTE_REQUIRED = ("code",)


class CoderAgent(BaseRoleAgent):
    """Agent that writes code for a plan — either a one-shot script or a reusable tool."""

    coder: Callable[[AgentState], dict[str, Any]]

    def __init__(self, coder: Callable[[AgentState], dict[str, Any]] | None = None) -> None:
        super().__init__(name="code", role="coder", prompt_template="coder.txt")
        self.coder = coder or _default_coder

    def run(self, state: AgentState) -> AgentResult:
        """Write code for the current plan and route to execution.

        Handles two modes based on state.current_plan["tool_name"]:
        - "code_execute": generates an inline script; only "code" is required.
        - "tool_create": generates a reusable run() function; "name", "description",
          and "code" are all required.
        """

        state.record_event("agent_started", agent_role=self.role, node=self.name)

        tool_name = state.current_plan.get("tool_name", "tool_create")
        is_code_execute = tool_name == "code_execute"

        generated = self.coder(state)
        if isinstance(generated, dict):
            generated = _unwrap_double_encoded_code(generated)
        code = generated.get("code") if isinstance(generated, dict) else None
        if isinstance(code, str):
            state.generated_code = code

        arguments = dict(state.current_plan.get("arguments") or {})
        if isinstance(generated, dict):
            for key in ("code", "description", "parameters", "returns", "sample_arguments"):
                if key in generated and key not in arguments:
                    arguments[key] = generated[key]

        required_fields = _CODE_EXECUTE_REQUIRED if is_code_execute else _TOOL_CREATE_REQUIRED
        missing = _missing_fields(arguments, required_fields)
        if missing:
            state.next_node = "error"
            state.error_log = f"{tool_name} 인자가 부족합니다: " + ", ".join(missing)
            state.record_event(
                "coder_arguments_invalid",
                agent_role=self.role,
                missing_fields=missing,
            )
            state.record_event("agent_finished", agent_role=self.role, node=self.name, next_node=state.next_node)
            return AgentResult(
                next_node=state.next_node,
                status="invalid_arguments",
                details={"generated": generated, "missing_fields": missing},
            )

        state.current_plan = {"action": "tool", "tool_name": tool_name, "arguments": arguments}
        state.next_node = "execute"

        event_details: dict[str, Any] = {"agent_role": self.role, "code": code}
        if not is_code_execute:
            event_details["tool_name"] = arguments.get("name")
        state.record_event("tool_code_created", **event_details)
        state.record_event("agent_finished", agent_role=self.role, node=self.name, next_node=state.next_node)
        return AgentResult(next_node=state.next_node, details={"generated": generated})


def _unwrap_double_encoded_code(generated: dict[str, Any]) -> dict[str, Any]:
    """Unwrap double-encoded code produced by models that nest the output JSON inside the code field.

    Some smaller LLMs output {"code": "{\"code\": \"import json...\"}"} instead of
    {"code": "import json..."}.  We detect this by trying to JSON-parse the code value
    and, when the result is a dict with a "code" key, replacing the outer code field
    with the inner one.  Only one level of unwrapping is applied to avoid masking real errors.

    After _loads_plan_json parsing, Python string values contain actual control characters
    (real newlines, not \\n).  We normalise those before retrying json.loads.
    """
    code = generated.get("code")
    if not isinstance(code, str):
        return generated
    stripped = code.strip()
    if not stripped.startswith("{"):
        return generated

    def _try_parse(text: str) -> dict[str, Any] | None:
        # raw_decode tolerates trailing content after a valid JSON object.
        try:
            obj, _ = json.JSONDecoder().raw_decode(text)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
        # Some LLMs append stray ")" or "'" after a valid JSON string close quote,
        # and omit the outer object's closing "}".  Strip only the trailing garbage
        # (never strip '"' — it may be the string close we need) and repair.
        trimmed = text.rstrip("') \t\r\n")
        if trimmed.endswith('"'):
            for suffix in ("}", '"}', '"}}'):
                try:
                    obj, _ = json.JSONDecoder().raw_decode(trimmed + suffix)
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    continue
        return None

    inner = _try_parse(stripped) or _try_parse(_escape_control_chars(stripped))
    if not isinstance(inner, dict) or not isinstance(inner.get("code"), str):
        return generated

    unwrapped = dict(generated)
    unwrapped["code"] = inner["code"]
    for key in ("description", "parameters", "returns", "sample_arguments"):
        if key in inner and key not in unwrapped:
            unwrapped[key] = inner[key]
    return unwrapped


def _escape_control_chars(text: str) -> str:
    """Replace bare control characters inside JSON string literals with JSON escape sequences.

    Mirrors the relevant subset of AdaptiveAgent._normalize_json_control_chars so the
    unwrapper can handle code values that contain actual newlines after JSON parsing.
    """
    _CTRL = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
    result: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if escaped:
            escaped = False
            result.append(ch)
            continue
        if ch == "\\" and in_string:
            escaped = True
            result.append(ch)
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string and ch in _CTRL:
            result.append(_CTRL[ch])
        else:
            result.append(ch)
    return "".join(result)


def _missing_fields(arguments: dict[str, Any], required: tuple[str, ...]) -> list[str]:
    """Return required fields that are absent or blank."""

    return [k for k in required if not isinstance(arguments.get(k), str) or not arguments[k].strip()]


def _default_coder(_state: AgentState) -> dict[str, Any]:
    return {}
