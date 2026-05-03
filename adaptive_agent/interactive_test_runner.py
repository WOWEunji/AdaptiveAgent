"""In-process interactive scenario test runner."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from pprint import pformat
from typing import TYPE_CHECKING

from adaptive_agent.agent import AdaptiveAgent
from adaptive_agent.config import AgentConfig
from adaptive_agent.conversation import ConversationSession
from adaptive_agent.tee_stream import TeeStream

if TYPE_CHECKING:
    from adaptive_agent.scenarios.interactive import InteractiveScenario


def run_interactive_scenarios(
    scenarios: tuple["InteractiveScenario", ...],
    *,
    config: AgentConfig,
) -> None:
    """Run scenarios in order. No pass/fail judgement — log only."""

    # S-01과 S-02는 같은 workspace 공유 — S-01 실행 후 workspace를 S-02에 전달
    shared_workspace: Path | None = None

    for scenario in scenarios:
        _run_scenario(scenario, config=config, shared_workspace=shared_workspace)

        if scenario.scenario_id == "S-01":
            shared_workspace = config.workspace_dir
        elif scenario.scenario_id == "S-02":
            shared_workspace = None


def _run_scenario(
    scenario: "InteractiveScenario",
    *,
    config: AgentConfig,
    shared_workspace: Path | None,
) -> None:
    from dataclasses import replace
    from adaptive_agent.logging import AgentLogger

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_dir = config.log_dir / "test" / "interactive"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{ts}_{scenario.scenario_id}.log"

    sep = "═" * 60
    with open(log_path, "w", encoding="utf-8") as log_fh:
        tee = TeeStream(sys.stderr, log_fh)

        def tee_print(text: str) -> None:
            tee.write(text + "\n")
            tee.flush()

        tee_print(f"\n{sep}")
        tee_print(f"  시나리오: [{scenario.scenario_id}] {scenario.title}")
        tee_print(f"  로그: {log_path}")
        tee_print(f"{sep}\n")

        if shared_workspace is not None:
            run_config = replace(
                config,
                workspace_dir=shared_workspace,
                tool_library_dir=shared_workspace / "skills",
                log_dir=shared_workspace / "logs",
                artifact_dir=shared_workspace / "artifacts",
            )
        else:
            run_config = config

        jsonl_path = run_config.log_dir / f"{ts}_{scenario.scenario_id}.jsonl"
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)

        logger = AgentLogger(quiet=False, log_file=jsonl_path, stream=tee)
        agent = AdaptiveAgent(config=run_config, logger=logger)
        session = ConversationSession()

        for turn_input in scenario.turns:
            tee_print(f"> {turn_input}")
            result = agent.run_turn(turn_input, session)
            _print_repl_result_to(result, tee_print)

            # approval_required 처리
            if getattr(result, "action", "") == "approval_required":
                ans = scenario.hitl_responses.get("approval_required", "")
                tool_name = (
                    (result.output or {}).get("name", "")
                    if isinstance(result.output, dict)
                    else ""
                )
                tee_print(f"  → 승인할까요? 네 / 아니오 > {ans}")
                if ans and any(kw in ans.lower() for kw in {"네", "예", "yes", "y"}):
                    approval_result = agent.run_tool("tool_approve", {"name": tool_name})
                    if approval_result.success:
                        tee_print(f"  '{tool_name}' 스킬이 저장되었습니다.")
                        session.pending_saves = [
                            s for s in session.pending_saves
                            if s.suggested_name != tool_name
                        ]
                    else:
                        tee_print(f"  저장 실패: {approval_result.error}")
                continue

            # ask_human 처리
            if getattr(result, "action", "") == "ask_human" or (
                getattr(result, "action", "") == "tool"
                and getattr(result, "tool_name", "") == "ask_human"
            ):
                ans = scenario.hitl_responses.get("ask_human", "")
                questions = []
                if isinstance(result.output, dict):
                    questions = result.output.get("questions") or []
                for q in (questions or ["추가 정보를 입력해 주세요."]):
                    tee_print(f"  {q}")
                tee_print(f"  > {ans}")
                if ans:
                    result2 = agent.run_turn(ans, session)
                    _print_repl_result_to(result2, tee_print)
                continue

        logger.close()
        tee_print(f"\n{sep}")
        tee_print(f"  [{scenario.scenario_id}] 완료")
        tee_print(f"{sep}\n")


def _print_repl_result_to(result, print_fn) -> None:
    """Delegate result output to print_fn (mirrors _print_repl_result in cli.py)."""
    summary = getattr(result, "summary", "")
    if summary:
        print_fn(summary)
        return
    output = result.output
    if isinstance(output, (dict, list)):
        print_fn(pformat(output, width=100, sort_dicts=False))
    elif output is not None:
        print_fn(str(output))
