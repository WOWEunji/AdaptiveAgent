"""Command line interface for AdaptiveAgent."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pprint import pformat
from typing import Sequence

from adaptive_agent.agent import AdaptiveAgent
from adaptive_agent.config import AgentConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="adaptive-agent",
        description="CLI 기반 Adaptive AI Agent 골격 실행기",
    )
    parser.add_argument("task", nargs="*", help="에이전트에 전달할 자연어 작업")
    parser.add_argument(
        "--llm",
        default=None,
        help="사용할 LLM 백엔드(기본: 환경 변수 또는 ollama)",
    )
    parser.add_argument(
        "--language",
        default="ko",
        choices=("ko", "en"),
        help="응답 언어",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="실행 결과를 JSON으로 출력",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="등록된 내장 툴 목록을 출력",
    )
    parser.add_argument(
        "--tool",
        default=None,
        help="LLM 계획 없이 명시적으로 실행할 툴 이름",
    )
    parser.add_argument(
        "--arg",
        action="append",
        default=[],
        help="명시 툴 실행 인자. key=value 형식이며 여러 번 사용할 수 있습니다.",
    )
    parser.add_argument(
        "--resume",
        default=None,
        help="pending HITL 세션 ID를 명시적으로 재개",
    )
    parser.add_argument(
        "--approve",
        action="store_true",
        help="재개한 pending 세션의 요청을 승인",
    )
    parser.add_argument(
        "--reject",
        action="store_true",
        help="재개한 pending 세션의 요청을 거부",
    )
    parser.add_argument(
        "--input",
        default=None,
        help="재개한 pending 세션에 전달할 추가 입력",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = AgentConfig.from_env(language=args.language)
    if args.llm:
        config = replace(config, llm_provider=args.llm)

    agent = AdaptiveAgent(config=config)

    if args.resume:
        try:
            result = agent.resume(args.resume, user_input=args.input, approve=args.approve, reject=args.reject)
        except ValueError as exc:
            if args.json:
                print(
                    json.dumps(
                        {
                            "success": False,
                            "error": str(exc),
                            "action": "resume_error",
                            "session_id": args.resume,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print(f"세션 재개 실패 (session_id={args.resume}): {exc}")
            return 1
        _print_result(result, args.json)
        return 0

    if args.list_tools:
        tools = agent.list_tools()
        if args.json:
            payload: object = [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "category": tool.category,
                            "requires_llm": tool.requires_llm,
                            "safety_level": tool.safety_level,
                            "usage": tool.usage,
                            "source": tool.source,
                        }
                        for tool in tools
                    ]
            load_results = getattr(agent.registry, "generated_load_results", [])
            if load_results:
                payload = {"tools": payload, "generated_load_results": load_results}
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for tool in tools:
                print(f"- {tool.name} [{tool.category}/{tool.source}]: {tool.description}")
        return 0

    if args.tool:
        result = agent.run_tool(args.tool, _parse_tool_args(args.arg))
        _print_result(result, args.json, tool_name=args.tool)
        return 0

    if not args.task:
        parser.error("task를 입력하세요. 예: adaptive-agent '현재 디렉터리 파일 목록 보여줘'")
    if len(args.task) > 1:
        parser.error("사용자 입력 원문 보존을 위해 task 전체를 따옴표로 감싸 하나의 인자로 전달하세요.")

    task = args.task[0]
    result = agent.run(task)
    _print_result(result, args.json)
    return 0


def _parse_tool_args(raw_args: Sequence[str]) -> dict[str, object]:
    """Parse ``key=value`` arguments for explicit tool execution.

    Values that look like JSON (start with ``{``/``[``/digit/``-``, or are
    exactly ``true``/``false``/``null``) are JSON-decoded so that nested
    structures (e.g. ``--arg sample_arguments={"x":7}``) reach the tool as
    real ``dict``/``list``/number/bool/None instead of opaque strings.
    Plain strings are kept as-is, preserving backwards compatibility.
    """

    parsed: dict[str, object] = {}
    for raw_arg in raw_args:
        key, separator, value = raw_arg.partition("=")
        if not separator:
            parsed[raw_arg] = True
        else:
            parsed[key] = _coerce_arg_value(value)
    return parsed


def _coerce_arg_value(value: str) -> object:
    """Decode a CLI ``--arg`` value as JSON when it looks like JSON."""

    stripped = value.strip()
    if not stripped:
        return value
    if stripped in {"true", "false", "null"} or stripped[0] in "{[-0123456789":
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return value
    return value


def _print_result(result, as_json: bool, tool_name: str | None = None) -> None:
    if as_json:
        print(json.dumps(_result_to_dict(result, tool_name=tool_name), ensure_ascii=False, indent=2))
    else:
        if isinstance(result.output, (dict, list)):
            print(pformat(result.output, width=100, sort_dicts=False))
        else:
            print(result.output)
        if hasattr(result, "action"):
            print(f"\n상태: {result.action}")
        if getattr(result, "tool_name", None):
            print(f"툴: {result.tool_name}")
        pending = getattr(result, "pending", None)
        if isinstance(pending, dict) and pending.get("status") == "pending":
            session_id = pending.get("session_id")
            print(f"세션: {session_id}")
            print(f"재개 예: python3 -m adaptive_agent --resume {session_id} --input \"...\"")
            print(f"거부 예: python3 -m adaptive_agent --resume {session_id} --reject")
            print("세션 파일이 누적되면 .adaptive_agent/sessions/ 아래 파일을 수동 삭제할 수 있습니다.")


def _result_to_dict(result, tool_name: str | None = None) -> dict[str, object]:
    if hasattr(result, "task"):
        events = getattr(result, "events", [])
        if not isinstance(events, list):
            events = []
        session_id = getattr(result, "session_id", None)
        pending = getattr(result, "pending", None)
        return {
            "task": result.task,
            "output": result.output,
            "tool_name": result.tool_name,
            "action": result.action,
            "session_id": session_id if isinstance(session_id, str) else None,
            "pending": pending if isinstance(pending, dict) else None,
            "events": [
                {
                    "name": event.name,
                    "details": event.details,
                    "created_at": event.created_at,
                }
                for event in events
            ],
        }

    return {
        "success": result.success,
        "output": result.output,
        "error": result.error,
        "tool_name": tool_name,
        "action": "tool" if tool_name else "tool_result",
        "execution_summary": _execution_summary(result.output),
    }


def _execution_summary(output: object) -> dict[str, object] | None:
    if not isinstance(output, dict):
        return None
    execution = output.get("execution")
    if not isinstance(execution, dict):
        return None
    return {
        "exit_code": execution.get("exit_code"),
        "stdout": execution.get("stdout"),
        "stderr": execution.get("stderr"),
        "timed_out": execution.get("timed_out"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
