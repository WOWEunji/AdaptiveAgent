"""Command line interface for AdaptiveAgent."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    task = " ".join(args.task).strip()
    if not task:
        parser.error("task를 입력하세요. 예: adaptive-agent '현재 디렉터리 파일 목록 보여줘'")

    config = AgentConfig.from_env(language=args.language)
    if args.llm:
        config = replace(config, llm_provider=args.llm)

    agent = AdaptiveAgent(config=config)
    result = agent.run(task)

    if args.json:
        print(
            json.dumps(
                {
                    "task": result.task,
                    "output": result.output,
                    "tool_name": result.tool_name,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(result.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
