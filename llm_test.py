#!/usr/bin/env python3
"""OpenAI / Gemini 스모크 테스트 (저가 모델 기본).

사용 예:
  python llm_test.py
  python llm_test.py openai
  python llm_test.py gemini
  python llm_test.py all --prompt "한 문장으로 인사해줘"

지원 provider: openai, gemini (ollama는 adaptive_agent CLI 사용 권장)

모델 후보(주석 — 환경변수 OPENAI_MODEL / GEMINI_MODEL 로 전환):

  OpenAI — 저가 나노·미니 (USD/1M 토큰; 합산 = Input + Output, 캐시 미적용 기준)
    | 모델           | Input | Cached In | Output | Input+Output |
    |----------------|-------|-----------|--------|--------------|
    | gpt-5-nano     | $0.05 | $0.005    | $0.40  | $0.45 ← 기본값 |
    | gpt-4.1-nano   | $0.10 | $0.025    | $0.40  | $0.50        |
    | gpt-4o-mini    | $0.15 | $0.075    | $0.60  | $0.75        |
    구현 참고: gpt-5* 는 Chat Completions 대신 Responses API 경로를 탄다 (`openai_client.py`).
    기타: gpt-3.5-turbo 등은 OPENAI_MODEL 로 지정 (OpenAI 요금표 확인)

  Gemini (저렴한 쪽 우선; 2026년 기준 2.0 Flash 계열 단종 일정 있음 → 2.5 Flash-Lite 권장)
    - gemini-2.5-flash-lite   ← 기본값 (GA, Flash-Lite)
    - gemini-2.0-flash-lite
    - gemini-2.0-flash
    - gemini-1.5-flash
    - gemini-2.5-flash        (더 비쌈, 필요 시)
"""

from __future__ import annotations

import argparse
import os
import sys

from adaptive_agent.config import AgentConfig
from adaptive_agent.llms.factory import create_llm_client


def _run(provider: str, prompt: str) -> str:
    cfg = AgentConfig.from_env()
    client = create_llm_client(cfg, provider=provider)
    return client.generate(prompt)


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenAI / Gemini LLM 스모크 테스트")
    parser.add_argument(
        "provider",
        nargs="?",
        default="all",
        choices=["all", "openai", "gemini"],
        help="테스트할 provider (기본: 키가 있는 것 모두)",
    )
    parser.add_argument(
        "--prompt",
        default="Reply with exactly one word: ok",
        help="보낼 사용자 프롬프트",
    )
    args = parser.parse_args()
    cfg = AgentConfig.from_env()

    targets: list[str]
    if args.provider == "all":
        targets = []
        if os.getenv("OPENAI_API_KEY"):
            targets.append("openai")
        gem_env = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if gem_env:
            targets.append("gemini")
        if not targets:
            print(
                "OPENAI_API_KEY 또는 GEMINI_API_KEY/GOOGLE_API_KEY 가 없습니다. .env 를 확인하세요.",
                file=sys.stderr,
            )
            return 1
    else:
        targets = [args.provider]

    exit_code = 0
    for name in targets:
        print(f"=== {name} ===")
        model_id = cfg.openai_model if name == "openai" else cfg.gemini_model
        print(f"model: {model_id}")
        sys.stdout.flush()
        try:
            out = _run(name, args.prompt)
            print(out.strip() or "(빈 응답)")
            print()
        except Exception as e:  # noqa: BLE001 - 스크립트 진단용
            print(f"FAIL: {e}", file=sys.stderr)
            sys.stderr.flush()
            exit_code = 1
        sys.stdout.flush()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
