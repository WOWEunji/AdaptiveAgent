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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = AgentConfig.from_env(language=args.language)
    if args.llm:
        config = replace(config, llm_provider=args.llm)

    agent = AdaptiveAgent(config=config)

    if args.list_tools:
        tools = agent.list_tools()
        if args.json:
            print(
                json.dumps(
                    [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "category": tool.category,
                            "requires_llm": tool.requires_llm,
                            "safety_level": tool.safety_level,
                            "usage": tool.usage,
                        }
                        for tool in tools
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            for tool in tools:
                print(f"- {tool.name} [{tool.category}]: {tool.description}")
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
    """명시 툴 실행용 key=value 인자를 파싱합니다."""

    parsed: dict[str, object] = {}
    for raw_arg in raw_args:
        key, separator, value = raw_arg.partition("=")
        if not separator:
            parsed[raw_arg] = True
        else:
            parsed[key] = value
    return parsed


def _print_result(result, as_json: bool, tool_name: str | None = None) -> None:
    if as_json:
        print(json.dumps(_result_to_dict(result, tool_name=tool_name), ensure_ascii=False, indent=2))
    else:
        if isinstance(result.output, (dict, list)):
            print(pformat(result.output, width=100, sort_dicts=False))
        else:
            print(result.output)


def _result_to_dict(result, tool_name: str | None = None) -> dict[str, object]:
    if hasattr(result, "task"):
        return {
            "task": result.task,
            "output": result.output,
            "tool_name": result.tool_name,
            "action": result.action,
        }

    return {
        "success": result.success,
        "output": result.output,
        "error": result.error,
        "tool_name": tool_name,
        "action": "tool" if tool_name else "tool_result",
    }


if __name__ == "__main__":
    raise SystemExit(main())
