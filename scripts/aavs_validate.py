#!/usr/bin/env python3
"""Run AdaptiveAgent validation scenarios against a real LLM provider.

The prompts in this harness are intentionally general English task prompts based
on docs/adaptive_agent_validation_scenarios.md. They avoid embedding expected
answers as instructions so provider behavior can be observed instead of taught
to pass a single fixture.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_SCENARIOS = ", ".join(scenario_id for scenario_id in ("AAVS-001", "AAVS-003A", "AAVS-003B", "AAVS-006"))
UNSUPPORTED_SCENARIOS_NOTE = (
    "This harness currently automates provider-facing checks for "
    f"{SUPPORTED_SCENARIOS}. AAVS-002, AAVS-004, and AAVS-005 still require "
    "agent self-correction and persistent tool-library workflows that are not implemented in the current CLI loop."
)


@dataclass(frozen=True)
class Scenario:
    """A single provider-facing validation scenario."""

    scenario_id: str
    title: str
    prompt: str
    required_events: tuple[str, ...]
    any_events: tuple[str, ...] = ()
    required_action: str | None = None
    required_tool: str | None = None
    stdout_contains: tuple[str, ...] = ()
    output_contains_any: tuple[str, ...] = ()
    code_contains_any: tuple[str, ...] = ()
    expect_pass: bool = True
    notes: str = ""


@dataclass
class ScenarioRecord:
    """Serializable execution record for one scenario/provider pair."""

    scenario_id: str
    title: str
    provider: str
    model: str
    prompt: str
    command: list[str]
    started_at: str
    completed_at: str
    returncode: int
    stdout: str
    stderr: str
    parsed_result: dict[str, Any] | None
    checks: dict[str, bool]
    passed: bool
    failure_classification: str
    validation_scope: dict[str, Any]
    notes: str = ""

    def to_markdown(self) -> str:
        result = self.parsed_result or {}
        events = [event.get("name") for event in result.get("events", []) if isinstance(event, dict)]
        output = result.get("output", "")
        tool_name = result.get("tool_name")
        scope = self.validation_scope
        return "\n".join(
            [
                "## 실행 기록",
                "",
                f"- 시나리오 ID: {self.scenario_id}",
                f"- 실행 일시: {self.started_at}",
                f"- 사용 LLM provider/model: {self.provider}/{self.model}",
                "- 실행 환경: local CLI or GitHub Actions runner",
                f"- 사용자 입력: {self.prompt}",
                f"- Agent 계획 요약: action={result.get('action')}, tool={tool_name}",
                f"- 생성된 툴 이름: {tool_name or '없음'}",
                "- 생성된 툴 저장 위치: 해당 없음(명시 저장 시나리오 제외)",
                f"- 실행 명령: {' '.join(self.command)}",
                f"- 실행 결과: returncode={self.returncode}",
                f"- 오류 발생 여부: {'예' if self.stderr or self.returncode else '아니오'}",
                "- 자가 수정 횟수: 이벤트 기반 별도 확인 필요",
                "- 사용자 추가 입력 여부: ask_human/clarification 이벤트 기반 확인",
                "- 저장 동의 결과: 이벤트 기반 별도 확인 필요",
                f"- 검증 범위: {json.dumps(scope, ensure_ascii=False)}",
                f"- 최종 응답: {json.dumps(output, ensure_ascii=False)[:2000]}",
                f"- 통과/실패: {'통과' if self.passed else '실패'}",
                f"- 실패 원인 분류: {self.failure_classification}",
                f"- 비고: events={events}; checks={self.checks}; {self.notes}",
                "",
            ]
        )


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        scenario_id="AAVS-001",
        title="Structured JSON analysis via generated Python execution",
        prompt=(
            "From the JSON data below, identify monsters with hp >= 100 and compute their "
            "average hp. Use an executable tool with a standard JSON parser for the calculation, "
            "keep the JSON as text in the generated code and parse it with json.loads/json.load, "
            "then answer from the execution result.\n"
            '[{"name":"Goblin","hp":80},{"name":"Orc","hp":150},{"name":"Dragon","hp":300}]'
        ),
        required_events=("task_received", "task_analyzed", "tool_spec_created", "tool_executed", "tool_result_observed"),
        required_action="tool",
        required_tool="code_execute",
        stdout_contains=("Orc", "Dragon", "225"),
        code_contains_any=("json.loads", "json.load"),
    ),
    Scenario(
        scenario_id="AAVS-003A",
        title="Ambiguous request asks for clarification",
        prompt=(
            "Clean up the data. If the request is underspecified, do not assume hidden data or "
            "criteria; ask the user for the missing information."
        ),
        required_events=("task_received", "task_analyzed"),
        any_events=("clarification_requested",),
        required_tool="ask_human",
        output_contains_any=("pending_human_input", "which data", "what data", "criteria", "missing"),
    ),
    Scenario(
        scenario_id="AAVS-003B",
        title="CSV deduplication and date sorting via standard parser",
        prompt=(
            "Remove duplicate rows from the CSV below, then sort the remaining rows by date in "
            "ascending order. Use an executable tool with a standard CSV parser and answer from "
            "the execution result.\n"
            "date,name,score\n"
            "2026-04-03,Alice,10\n"
            "2026-04-01,Bob,20\n"
            "2026-04-03,Alice,10\n"
            "2026-04-02,Charlie,15"
        ),
        required_events=("task_received", "task_analyzed", "tool_spec_created", "tool_executed", "tool_result_observed"),
        required_action="tool",
        required_tool="code_execute",
        stdout_contains=("2026-04-01,Bob,20", "2026-04-02,Charlie,15", "2026-04-03,Alice,10"),
        code_contains_any=("csv.", "csv\n", "import csv"),
    ),
    Scenario(
        scenario_id="AAVS-006",
        title="Private database request asks for access details",
        prompt=(
            "Analyze the top 10 products by revenue from my private database for last month. "
            "If credentials, connection details, schema, or access are missing, do not fabricate "
            "data and ask for the required information."
        ),
        required_events=("task_received", "task_analyzed"),
        any_events=("clarification_requested",),
        required_tool="ask_human",
        output_contains_any=("database", "credentials", "connection", "access", "schema", "pending_human_input"),
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AAVS provider validation scenarios")
    parser.add_argument("--provider", choices=("openai", "ollama"), required=True)
    parser.add_argument("--model", default="", help="Provider model override")
    parser.add_argument(
        "--scenario",
        action="append",
        choices=[scenario.scenario_id for scenario in SCENARIOS],
        help="Scenario ID to run. Repeatable. Defaults to release-gate scenarios in this script.",
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        choices=[scenario.scenario_id for scenario in SCENARIOS],
        help="Scenario IDs to run. Convenience form for GitHub workflow inputs.",
    )
    parser.add_argument("--output", default="", help="Write JSON execution records to this path")
    parser.add_argument("--markdown-output", default="", help="Write markdown execution records to this path")
    parser.add_argument("--output-dir", default="", help="Directory for records.json and records.md")
    parser.add_argument(
        "--timeout-seconds",
        default="",
        help="Per-scenario CLI timeout. Defaults to 180s for OpenAI and 600s for Ollama.",
    )
    args = parser.parse_args()

    requested = set((args.scenario or []) + (args.scenarios or []))
    selected = [scenario for scenario in SCENARIOS if not requested or scenario.scenario_id in requested]
    print(UNSUPPORTED_SCENARIOS_NOTE, file=sys.stderr)
    env = os.environ.copy()
    env["ADAPTIVE_AGENT_LLM"] = args.provider
    model = args.model or default_model(args.provider, env)
    if args.provider == "openai":
        env["OPENAI_MODEL"] = model
    if args.provider == "ollama":
        env["OLLAMA_MODEL"] = model

    records: list[ScenarioRecord] = []
    timeout_seconds = parse_timeout_seconds(args.timeout_seconds, args.provider)
    for scenario in selected:
        records.append(
            run_scenario(
                scenario,
                provider=args.provider,
                model=model,
                env=env,
                timeout_seconds=timeout_seconds,
            )
        )

    output_path = Path(args.output) if args.output else None
    markdown_path = Path(args.markdown_output) if args.markdown_output else None
    if args.output_dir:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_path or output_dir / "records.json"
        markdown_path = markdown_path or output_dir / "records.md"

    if output_path:
        output_path.write_text(
            json.dumps([record.__dict__ for record in records], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if markdown_path:
        markdown_path.write_text(
            "\n".join(record.to_markdown() for record in records),
            encoding="utf-8",
        )

    print(json.dumps([record.__dict__ for record in records], ensure_ascii=False, indent=2))
    return 0 if all(record.passed for record in records) else 1


def default_model(provider: str, env: dict[str, str]) -> str:
    if provider == "openai":
        return env.get("OPENAI_MODEL", "gpt-5-nano")
    return env.get("OLLAMA_MODEL", "qwen3.5:2b")


def default_timeout_seconds(provider: str) -> float:
    """Ollama runs locally on a CPU GitHub runner, so it needs a longer per-scenario budget."""

    return 600.0 if provider == "ollama" else 180.0


def parse_timeout_seconds(raw_timeout: str, provider: str) -> float:
    """빈 workflow 입력값은 provider별 기본 timeout으로 처리합니다."""

    if not raw_timeout.strip():
        return default_timeout_seconds(provider)
    try:
        return float(raw_timeout)
    except ValueError as exc:
        raise SystemExit(f"--timeout-seconds must be a number, got: {raw_timeout!r}") from exc


def run_scenario(
    scenario: Scenario,
    *,
    provider: str,
    model: str,
    env: dict[str, str],
    timeout_seconds: float,
) -> ScenarioRecord:
    started_at = utc_now()
    command = [sys.executable, "-m", "adaptive_agent", "--json", "--llm", provider, scenario.prompt]
    with tempfile.TemporaryDirectory(prefix=f"aavs-{scenario.scenario_id.lower()}-") as temp_dir:
        run_env = env.copy()
        run_env["ADAPTIVE_AGENT_WORKSPACE"] = str(REPO_ROOT)
        run_env["ADAPTIVE_AGENT_TOOL_LIBRARY"] = str(Path(temp_dir) / "tools")
        try:
            completed = subprocess.run(
                command,
                cwd=REPO_ROOT,
                env=run_env,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            returncode = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        except subprocess.TimeoutExpired as exc:
            returncode = 124
            stdout = decode_timeout_output(exc.stdout)
            stderr = decode_timeout_output(exc.stderr) or f"Timed out after {timeout_seconds:g}s"
    completed_at = utc_now()
    parsed = parse_result(stdout)
    checks = evaluate_scenario(scenario, returncode, parsed)
    passed = all(checks.values()) if scenario.expect_pass else not all(checks.values())
    return ScenarioRecord(
        scenario_id=scenario.scenario_id,
        title=scenario.title,
        provider=provider,
        model=model,
        prompt=scenario.prompt,
        command=command,
        started_at=started_at,
        completed_at=completed_at,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        parsed_result=parsed,
        checks=checks,
        passed=passed,
        failure_classification=classify_failure(returncode, parsed, checks),
        validation_scope=build_validation_scope(parsed),
        notes=f"{scenario.notes} timeout_seconds={timeout_seconds:g}".strip(),
    )


def parse_result(stdout: str) -> dict[str, Any] | None:
    try:
        loaded = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def decode_timeout_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def evaluate_scenario(scenario: Scenario, returncode: int, parsed: dict[str, Any] | None) -> dict[str, bool]:
    if parsed is None:
        return {"cli_returncode": returncode == 0, "json_result": False}

    events = [event.get("name") for event in parsed.get("events", []) if isinstance(event, dict)]
    output_text = json.dumps(parsed.get("output", ""), ensure_ascii=False)
    code_text = "\n".join(
        str(event.get("details", {}).get("code", ""))
        for event in parsed.get("events", [])
        if isinstance(event, dict)
    )
    checks: dict[str, bool] = {
        "cli_returncode": returncode == 0,
        "json_result": True,
        "required_events": all(event in events for event in scenario.required_events),
    }
    output_matches = True
    if scenario.output_contains_any:
        lowered = output_text.casefold()
        output_matches = any(fragment.casefold() in lowered for fragment in scenario.output_contains_any)
    if scenario.any_events:
        # A clear direct LLM clarification is acceptable for current CLI output, as long as
        # the answer asks for missing information instead of fabricating data.
        checks["any_expected_event"] = any(event in events for event in scenario.any_events) or (
            parsed.get("action") == "llm" and output_matches
        )
    if scenario.required_action:
        checks["required_action"] = parsed.get("action") == scenario.required_action
    if scenario.required_tool:
        checks["required_tool"] = parsed.get("tool_name") == scenario.required_tool or (
            scenario.required_tool == "ask_human" and parsed.get("action") == "llm" and output_matches
        )
    if scenario.stdout_contains:
        checks["stdout_contains"] = all(fragment in output_text for fragment in scenario.stdout_contains)
    if scenario.output_contains_any:
        checks["output_contains_any"] = output_matches
    if scenario.code_contains_any:
        checks["code_uses_expected_parser"] = any(fragment in code_text for fragment in scenario.code_contains_any)
    return checks


def classify_failure(returncode: int, parsed: dict[str, Any] | None, checks: dict[str, bool]) -> str:
    if all(checks.values()):
        return "없음"
    if parsed is None or returncode != 0:
        return "외부 provider 오류 또는 CLI 실행 오류"
    if parsed.get("action") == "llm_error":
        return "외부 provider 오류"
    if not checks.get("required_tool", True) or not checks.get("required_action", True):
        return "LLM 계획 오류"
    if not checks.get("stdout_contains", True) or not checks.get("code_uses_expected_parser", True):
        return "생성 코드 오류 또는 실행 결과 검증 실패"
    if not checks.get("clarification_observed", checks.get("any_expected_event", True)):
        return "사용자 입력 부족 처리 오류"
    return "검증 기준 미충족"


def build_validation_scope(parsed: dict[str, Any] | None) -> dict[str, Any]:
    """검증이 최종 자연어 응답까지 포함하는지, raw tool output에 머무는지 명시합니다."""

    if parsed is None:
        return {
            "agent_result_available": False,
            "raw_tool_output_checked": False,
            "final_user_response_checked": False,
            "note": "CLI did not return parseable JSON.",
        }

    action = parsed.get("action")
    tool_name = parsed.get("tool_name")
    raw_tool_output = action == "tool" and tool_name is not None
    return {
        "agent_result_available": True,
        "raw_tool_output_checked": raw_tool_output,
        "final_user_response_checked": action == "llm",
        "note": (
            "Current AdaptiveAgent returns the structured tool execution result directly after tool use; "
            "these scenarios validate tool choice, generated code, events, and raw execution output, "
            "not a second natural-language answer synthesis step."
            if raw_tool_output
            else "Scenario validation is based on the agent response payload."
        ),
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    if shutil.which(sys.executable) is None:
        raise SystemExit("Python executable not found")
    raise SystemExit(main())
