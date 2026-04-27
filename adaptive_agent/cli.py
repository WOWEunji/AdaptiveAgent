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
                            "keywords": list(tool.keywords),
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

    task = " ".join(args.task).strip()
    if not task:
        parser.error("task를 입력하세요. 예: adaptive-agent '현재 디렉터리 파일 목록 보여줘'")

    result = agent.run(task)

    if args.json:
        print(
            json.dumps(
                {
                    "task": result.task,
                    "output": result.output,
                    "tool_name": result.tool_name,
                    "action": result.action,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        if isinstance(result.output, (dict, list)):
            print(pformat(result.output, width=100, sort_dicts=False))
        else:
            print(result.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
