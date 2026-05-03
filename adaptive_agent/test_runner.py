"""AAVS scenario runner: executes scenarios against a live LLM provider and writes reports."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adaptive_agent.scenarios.aavs import (
    SCENARIOS,
    SCENARIO_IDS,
    Scenario,
    ScenarioRecord,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

_PASS = "\033[32m✓ PASS\033[0m"
_FAIL = "\033[31m✗ FAIL\033[0m"
_SKIP = "\033[33m─ SKIP\033[0m"


# ── public API ────────────────────────────────────────────────────────────────

def run_all(
    scenarios: tuple[Scenario, ...],
    *,
    provider: str,
    model: str,
    env: dict[str, str],
    timeout_seconds: float,
    workspace_dir: Path | None = None,
    verbose: bool = True,
) -> list[ScenarioRecord]:
    """Run each scenario and return the collected records."""
    records: list[ScenarioRecord] = []
    total = len(scenarios)
    for idx, scenario in enumerate(scenarios, 1):
        if verbose:
            _print_scenario_header(idx, total, scenario)
        record = _run_scenario(
            scenario,
            provider=provider,
            model=model,
            env=env,
            timeout_seconds=timeout_seconds,
            workspace_dir=workspace_dir,
        )
        records.append(record)
        if verbose:
            _print_scenario_result(record)
    return records


def save_reports(records: list[ScenarioRecord], output_dir: Path) -> tuple[Path, Path]:
    """Write records.json and records.md to output_dir. Returns (json_path, md_path)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "records.json"
    md_path   = output_dir / "records.md"
    json_path.write_text(
        json.dumps([r.__dict__ for r in records], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(
        "\n".join(r.to_markdown() for r in records),
        encoding="utf-8",
    )
    return json_path, md_path


def print_summary(records: list[ScenarioRecord]) -> None:
    """Print a compact pass/fail table to stdout."""
    passed = sum(1 for r in records if r.passed)
    failed = len(records) - passed
    sep = "─" * 60
    print(f"\n{sep}")
    print(f"  결과 요약  (통과 {passed}/{len(records)}  실패 {failed})")
    print(sep)
    for r in records:
        icon = _PASS if r.passed else _FAIL
        title = r.title[:42]
        print(f"  {icon}  {r.scenario_id:<10}  {title}")
    print(sep + "\n")


def default_model(provider: str, env: dict[str, str]) -> str:
    if provider == "openai":
        return env.get("OPENAI_MODEL", "gpt-5-nano")
    return env.get("OLLAMA_MODEL", "qwen3.5:2b")


def default_timeout(provider: str) -> float:
    return 600.0 if provider == "ollama" else 180.0


def parse_timeout(raw: str, provider: str) -> float:
    if not raw.strip():
        return default_timeout(provider)
    try:
        return float(raw)
    except ValueError as exc:
        raise SystemExit(f"--timeout-seconds must be a number, got: {raw!r}") from exc


# ── CLI entry point (used by adaptive-agent test and scripts/aavs_validate.py) ──

def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="aavs-runner",
        description="Run AAVS provider validation scenarios",
    )
    parser.add_argument("--provider", choices=("openai", "ollama"), required=True)
    parser.add_argument("--model", default="", help="Provider model override")
    parser.add_argument(
        "--scenario",
        action="append",
        choices=list(SCENARIO_IDS),
        dest="scenarios",
        metavar="ID",
        help="Scenario ID to run (repeatable). Defaults to all.",
    )
    parser.add_argument("--output-dir", default="", help="Write records.json + records.md here")
    parser.add_argument(
        "--timeout-seconds",
        default="",
        help="Per-scenario timeout. Default: 180s (OpenAI) or 600s (Ollama).",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress per-scenario progress lines")
    args = parser.parse_args(argv)

    requested = set(args.scenarios or [])
    selected  = tuple(s for s in SCENARIOS if not requested or s.scenario_id in requested)
    if not selected:
        parser.error("No matching scenarios found.")

    env = _build_env(args.provider, args.model)
    model = args.model or default_model(args.provider, env)
    timeout = parse_timeout(args.timeout_seconds, args.provider)

    records = run_all(
        selected,
        provider=args.provider,
        model=model,
        env=env,
        timeout_seconds=timeout,
        verbose=not args.quiet,
    )

    print_summary(records)

    if args.output_dir:
        json_path, md_path = save_reports(records, Path(args.output_dir))
        print(f"리포트 저장: {json_path}")
        print(f"마크다운   : {md_path}")

    return 0 if all(r.passed for r in records) else 1


# ── internal execution helpers ────────────────────────────────────────────────

def _build_env(provider: str, model: str) -> dict[str, str]:
    import os
    env = os.environ.copy()
    env["ADAPTIVE_AGENT_LLM"] = provider
    model_key = {"openai": "OPENAI_MODEL", "ollama": "OLLAMA_MODEL"}
    if model and provider in model_key:
        env[model_key[provider]] = model
    return env


def _run_scenario(
    scenario: Scenario,
    *,
    provider: str,
    model: str,
    env: dict[str, str],
    timeout_seconds: float,
    workspace_dir: Path | None = None,
) -> ScenarioRecord:
    started_at = _utc_now()
    command = [sys.executable, "-m", "adaptive_agent", "--llm", provider,
               "interactive", "--json", scenario.prompt]
    step2_parsed: dict[str, Any] | None = None
    step2_returncode: int | None = None

    with tempfile.TemporaryDirectory(prefix=f"aavs-{scenario.scenario_id.lower()}-") as tmp:
        run_env = env.copy()
        run_env["ADAPTIVE_AGENT_WORKSPACE"] = str(workspace_dir or REPO_ROOT)
        run_env["ADAPTIVE_AGENT_TOOL_LIBRARY"] = str(Path(tmp) / "tools")

        if scenario.setup_skills:
            _write_setup_manifest(Path(run_env["ADAPTIVE_AGENT_TOOL_LIBRARY"]), scenario.setup_skills)

        returncode, stdout, stderr = _subprocess_run(command, run_env, timeout_seconds)

        # step2: cross-session task
        if scenario.step2_task and returncode == 0:
            cmd2 = [sys.executable, "-m", "adaptive_agent", "--llm", provider,
                    "interactive", "--json", scenario.step2_task]
            rc2, out2, err2 = _subprocess_run(cmd2, run_env, timeout_seconds)
            step2_returncode = rc2
            step2_parsed = _parse_json(out2)
            if rc2:
                stderr = (stderr + f"\n[step2] {err2}").strip()

    completed_at = _utc_now()
    parsed = _parse_json(stdout)
    checks = _evaluate(
        scenario, returncode, parsed,
        step2_parsed=step2_parsed,
        step2_returncode=step2_returncode,
    )
    passed = all(checks.values()) if scenario.expect_pass else not all(checks.values())
    extra = ""
    if scenario.step2_task:
        extra = " step2_task=yes"
    if scenario.setup_skills:
        extra += f" setup_skills={len(scenario.setup_skills)}"

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
        failure_classification=_classify_failure(returncode, parsed, checks),
        validation_scope=_validation_scope(parsed),
        notes=f"{scenario.notes}{extra} timeout_seconds={timeout_seconds:g}".strip(),
    )


def _subprocess_run(
    command: list[str],
    env: dict[str, str],
    timeout: float,
) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return completed.returncode, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = (exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")) or f"Timed out after {timeout:g}s"
        return 124, stdout, stderr


def _write_setup_manifest(tools_dir: Path, setup_skills: tuple[dict, ...]) -> None:
    tools_dir.mkdir(parents=True, exist_ok=True)
    now = _utc_now()
    entries = []
    for skill in setup_skills:
        name = skill["name"]
        code = skill.get("code", "def run(arguments):\n    return {}\n")
        py_path = tools_dir / f"{name}.py"
        py_path.write_text(code, encoding="utf-8")
        file_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        entries.append({
            "name": name,
            "description": skill.get("description", ""),
            "category": "generated",
            "tags": skill.get("tags", []),
            "file_path": str(py_path),
            "file_hash": file_hash,
            "parameters": skill.get("parameters", {}),
            "returns": skill.get("returns", {}),
            "validation_status": "passed",
            "approval_status": "approved",
            "created_at": now,
            "updated_at": now,
            "usage_count": 0,
            "failure_count": 0,
            "reflections": [],
        })
    manifest = {"schema_version": 1, "tools": sorted(entries, key=lambda e: e["name"])}
    (tools_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _parse_json(stdout: str) -> dict[str, Any] | None:
    try:
        loaded = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _evaluate(
    scenario: Scenario,
    returncode: int,
    parsed: dict[str, Any] | None,
    *,
    step2_parsed: dict[str, Any] | None = None,
    step2_returncode: int | None = None,
) -> dict[str, bool]:
    if parsed is None:
        return {"cli_returncode": returncode == 0, "json_result": False}

    events = [e.get("name") for e in parsed.get("events", []) if isinstance(e, dict)]
    output_text = json.dumps(parsed.get("output", ""), ensure_ascii=False)
    code_text = "\n".join(
        str(e.get("details", {}).get("code", ""))
        for e in parsed.get("events", [])
        if isinstance(e, dict)
    )
    checks: dict[str, bool] = {
        "cli_returncode":  returncode == 0,
        "json_result":     True,
        "required_events": all(ev in events for ev in scenario.required_events),
    }

    output_matches = True
    if scenario.output_contains_any:
        lowered = output_text.casefold()
        output_matches = any(f.casefold() in lowered for f in scenario.output_contains_any)

    if scenario.any_events:
        checks["any_expected_event"] = (
            any(ev in events for ev in scenario.any_events)
            or (parsed.get("action") == "llm" and output_matches)
        )
    if scenario.required_action:
        checks["required_action"] = parsed.get("action") == scenario.required_action
    if scenario.required_tool:
        checks["required_tool"] = parsed.get("tool_name") == scenario.required_tool or (
            scenario.required_tool == "ask_human"
            and parsed.get("action") == "llm"
            and output_matches
        )
    if scenario.stdout_contains:
        checks["stdout_contains"] = all(f in output_text for f in scenario.stdout_contains)
    if scenario.output_contains_any:
        checks["output_contains_any"] = output_matches
    if scenario.code_contains_any:
        checks["code_uses_expected_parser"] = any(f in code_text for f in scenario.code_contains_any)

    if scenario.setup_skills:
        retrieval = [
            e for e in parsed.get("events", [])
            if isinstance(e, dict) and e.get("name") == "skills_retrieved"
        ]
        checks["skills_retrieved_count"] = (
            retrieval[0].get("details", {}).get("count", 0) >= 1 if retrieval else False
        )

    if scenario.step2_task:
        if step2_returncode is None:
            checks["step2_task_executed"]  = False
            checks["step2_skills_retrieved"] = False
        else:
            checks["step2_task_executed"] = step2_returncode == 0
            if step2_parsed:
                s2_ret = [
                    e for e in step2_parsed.get("events", [])
                    if isinstance(e, dict) and e.get("name") == "skills_retrieved"
                ]
                checks["step2_skills_retrieved"] = (
                    s2_ret[0].get("details", {}).get("count", 0) >= 1 if s2_ret else False
                )
            else:
                checks["step2_skills_retrieved"] = False

    return checks


def _classify_failure(returncode: int, parsed: dict[str, Any] | None, checks: dict[str, bool]) -> str:
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
    if not checks.get("any_expected_event", True):
        return "사용자 입력 부족 처리 오류"
    return "검증 기준 미충족"


def _validation_scope(parsed: dict[str, Any] | None) -> dict[str, Any]:
    if parsed is None:
        return {
            "agent_result_available": False,
            "raw_tool_output_checked": False,
            "final_user_response_checked": False,
            "note": "CLI did not return parseable JSON.",
        }
    action = parsed.get("action")
    tool_name = parsed.get("tool_name")
    raw_tool = action == "tool" and tool_name is not None
    return {
        "agent_result_available": True,
        "raw_tool_output_checked": raw_tool,
        "final_user_response_checked": action == "llm",
        "note": (
            "Validates tool choice, generated code, events, and raw execution output."
            if raw_tool else "Validation based on agent response payload."
        ),
    }


def _print_scenario_header(idx: int, total: int, scenario: Scenario) -> None:
    sep = "─" * 60
    print(f"\n{sep}")
    print(f"  [{idx}/{total}] {scenario.scenario_id}  {scenario.title}")
    print(sep)


def _print_scenario_result(record: ScenarioRecord) -> None:
    icon = _PASS if record.passed else _FAIL
    failed_checks = [k for k, v in record.checks.items() if not v]
    print(f"  {icon}  returncode={record.returncode}")
    if failed_checks:
        print(f"  실패 항목: {', '.join(failed_checks)}")
    if record.failure_classification != "없음":
        print(f"  분류: {record.failure_classification}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
