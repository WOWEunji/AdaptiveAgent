"""Command line interface for AdaptiveAgent."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from pprint import pformat
from typing import Sequence

from adaptive_agent.agent import AdaptiveAgent
from adaptive_agent.config import AgentConfig
from adaptive_agent.conversation import ConversationSession

_GLOBAL_OPTS_WITH_VAL = {"--workspace", "--llm", "--language"}
_GLOBAL_BOOL_FLAGS    = {"--quiet", "-h", "--help", "--list-skills"}
_KNOWN_COMMANDS       = {"interactive", "shell", "test"}


def _inject_default_command(raw: list[str]) -> list[str]:
    """Prepend 'interactive' when no known subcommand is detected."""
    i, insert_pos = 0, 0
    while i < len(raw):
        arg = raw[i]
        if arg in _GLOBAL_OPTS_WITH_VAL:
            i += 2; insert_pos = i
        elif any(arg.startswith(f"{opt}=") for opt in _GLOBAL_OPTS_WITH_VAL):
            i += 1; insert_pos = i
        elif arg in _GLOBAL_BOOL_FLAGS:
            i += 1; insert_pos = i
        else:
            if arg not in _KNOWN_COMMANDS:
                return raw[:insert_pos] + ["interactive"] + raw[insert_pos:]
            break
    return raw


# ── workspace ─────────────────────────────────────────────────────────────────

def _setup_workspace(ws_arg: str | None, config: AgentConfig) -> AgentConfig:
    if ws_arg:
        ws = Path(ws_arg).expanduser().resolve()
        config = replace(
            config,
            workspace_dir=ws,
            tool_library_dir=ws / "skills",
            artifact_dir=ws / "artifacts",
            log_dir=ws / "logs",
        )
    for sub in (config.tool_library_dir, config.artifact_dir, config.log_dir):
        sub.mkdir(parents=True, exist_ok=True)
    return config


def _new_log_file(config: AgentConfig) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return config.log_dir / f"{ts}.jsonl"


# ── interactive (REPL + inline HITL) ─────────────────────────────────────────

def _build_interactive_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "interactive",
        aliases=["shell"],
        help="작업 실행 (task 없이 실행하면 대화형 루프)",
    )
    p.add_argument("task", nargs="*", help="실행할 자연어 작업 (생략하면 대화형 루프 시작)")
    p.add_argument("--json", action="store_true", help="결과를 JSON으로 출력 (task 지정 시)")


def _run_interactive(args: argparse.Namespace) -> int:
    config = AgentConfig.from_env(language=args.language)
    if args.llm:
        config = replace(config, llm_provider=args.llm)
    config = _setup_workspace(args.workspace, config)

    from adaptive_agent.logging import AgentLogger

    is_single = bool(getattr(args, "task", None))
    log_file  = _new_log_file(config) if not (is_single and getattr(args, "json", False)) else None
    logger    = AgentLogger(quiet=args.quiet, log_file=log_file)

    try:
        agent = AdaptiveAgent(config=config, logger=logger)
        if is_single:
            return _single_shot(agent, args)

        _repl_loop(agent, config)
        return 0
    finally:
        logger.close()


def _single_shot(agent: AdaptiveAgent, args: argparse.Namespace) -> int:
    """Run one task and exit."""
    task = getattr(args, "task", [])
    if not task or not task[0].strip():
        print("task를 입력하세요.", file=sys.stderr)
        raise SystemExit(2)
    if len(task) > 1:
        print("task 전체를 따옴표로 감싸 하나의 인자로 전달하세요.", file=sys.stderr)
        raise SystemExit(2)

    result = agent.run(task[0])
    _print_result(result, getattr(args, "json", False))
    return 0


def _repl_loop(
    agent: AdaptiveAgent,
    config: AgentConfig,
    session: ConversationSession | None = None,
) -> None:
    try:
        import readline  # noqa: F401
    except ImportError:
        pass

    print(f"\nAdaptiveAgent 대화 모드")
    print(f"  workspace : {config.workspace_dir}")
    print(f"  provider  : {config.llm_provider}")
    print("  종료      : quit 또는 Ctrl+C\n")

    if session is None:
        session = ConversationSession()

    _clean_exit = False  # quit 명령으로 나간 경우만 True
    try:
        while True:
            try:
                try:
                    raw = input("> ")
                except EOFError:
                    print("\n종료합니다.")
                    _clean_exit = True
                    break

                # 멀티라인: 줄 끝 \이면 다음 줄 계속 입력
                while raw.endswith("\\"):
                    raw = raw[:-1]
                    try:
                        raw += "\n" + input("... ")
                    except EOFError:
                        break

                user_input = raw.strip()
                if not user_input:
                    continue
                if user_input.lower() in {"quit", "exit", "종료", "q"}:
                    print("종료합니다.")
                    _clean_exit = True
                    break

                if user_input.lower() in {"skills", "skill list", "스킬 목록", "스킬목록"}:
                    from adaptive_agent.skills import SkillCatalog
                    entries = SkillCatalog(config.tool_library_dir).list()
                    if not entries:
                        print("등록된 스킬 없음")
                    else:
                        for e in entries:
                            print(f"  {e.get('name'):<30} {str(e.get('description', ''))[:60]}")
                    continue

                result = agent.run_turn(user_input, session)
                _print_repl_result(result)

                if result.needs_input:
                    try:
                        ans = input(f"  → {result.input_prompt} > ").strip()
                    except (EOFError, KeyboardInterrupt):
                        ans = "no"
                    result = agent.run_turn(ans, session)
                    _print_repl_result(result)

            except KeyboardInterrupt:
                print("\n종료합니다.")
                break  # _clean_exit = False → 저장 프롬프트 스킵
            except Exception as exc:  # noqa: BLE001
                print(f"\n[오류] {exc}", file=sys.stderr)
    finally:
        pass


def _print_repl_result(result) -> None:
    summary = getattr(result, "summary", "")
    if summary:
        print(summary)
        return
    output = result.output
    if isinstance(output, (dict, list)):
        print(pformat(output, width=100, sort_dicts=False))
    elif output is not None:
        print(output)
    action = getattr(result, "action", "")
    if action and action not in ("tool", "llm", "approval_required"):
        print(f"  [상태: {action}]")


# ── test (interactive scenarios) ─────────────────────────────────────────────

def _build_test_parser(subparsers: argparse._SubParsersAction) -> None:
    subparsers.add_parser("test", help="인터랙티브 시나리오 테스트 실행")
    # 별도 인자 없음 — 전역 --workspace, --llm 사용


def _run_test(args: argparse.Namespace) -> int:
    from adaptive_agent.scenarios.interactive import INTERACTIVE_SCENARIOS
    from adaptive_agent.interactive_test_runner import run_interactive_scenarios

    provider = args.llm
    if not provider:
        print("test 모드에서는 --llm을 지정해야 합니다.", file=sys.stderr)
        return 2

    config = AgentConfig.from_env(language=args.language)
    if args.llm:
        config = replace(config, llm_provider=args.llm)
    config = _setup_workspace(args.workspace, config)

    run_interactive_scenarios(INTERACTIVE_SCENARIOS, config=config)
    return 0


# ── parser & entry point ──────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="adaptive-agent",
        description="CLI 기반 Adaptive AI Agent",
    )
    parser.add_argument("--workspace", default=None, metavar="PATH",
                        help="작업 공간 디렉토리 (없으면 자동 생성)")
    parser.add_argument("--llm", default=None,
                        help="LLM backend (기본: .env 또는 ollama)")
    parser.add_argument("--language", default="ko", choices=("ko", "en"),
                        help="응답 언어")
    parser.add_argument("--quiet", action="store_true",
                        help="진행 로그 숨김")
    parser.add_argument("--list-skills", action="store_true",
                        help="저장된 스킬 목록 출력 후 종료")

    sub = parser.add_subparsers(dest="command")
    _build_interactive_parser(sub)
    _build_test_parser(sub)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw  = list(argv if argv is not None else sys.argv[1:])
    raw  = _inject_default_command(raw)
    args = build_parser().parse_args(raw)

    if getattr(args, "list_skills", False):
        from adaptive_agent.skills import SkillCatalog
        config = AgentConfig.from_env()
        config = _setup_workspace(args.workspace, config)
        entries = SkillCatalog(config.tool_library_dir).list()
        if not entries:
            print("등록된 스킬 없음")
        else:
            for e in entries:
                print(f"  {e.get('name'):<30} {str(e.get('description', ''))[:60]}")
        return 0

    if args.command == "test":
        return _run_test(args)
    return _run_interactive(args)


# ── output helpers ───────────────────────────────────────────────────────────

def _print_result(result, as_json: bool, tool_name: str | None = None) -> None:
    if as_json:
        print(json.dumps(_result_to_dict(result, tool_name=tool_name),
                         ensure_ascii=False, indent=2))
        return
    output = result.output
    if isinstance(output, (dict, list)):
        print(pformat(output, width=100, sort_dicts=False))
    else:
        print(output)
    if hasattr(result, "action"):
        print(f"\n상태: {result.action}")
    if getattr(result, "tool_name", None):
        print(f"툴: {result.tool_name}")


def _result_to_dict(result, tool_name: str | None = None) -> dict[str, object]:
    if hasattr(result, "task"):
        events = getattr(result, "events", []) or []
        return {
            "task":      result.task,
            "output":    result.output,
            "tool_name": result.tool_name,
            "action":    result.action,
            "summary":   getattr(result, "summary", ""),
            "events": [
                {"name": e.name, "details": e.details, "created_at": e.created_at}
                for e in events
            ],
        }
    return {
        "success":           result.success,
        "output":            result.output,
        "error":             result.error,
        "tool_name":         tool_name,
        "action":            "tool" if tool_name else "tool_result",
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
        "stdout":    execution.get("stdout"),
        "stderr":    execution.get("stderr"),
        "timed_out": execution.get("timed_out"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
