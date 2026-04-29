"""AdaptiveAgent built-in tool implementations."""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from adaptive_agent.tools.models import ToolExecutionResult

_SUPPORTED_CODE_LANGS = {"python", "py"}
_SUPPORTED_SHELL_LANGS = {"bash", "sh", "shell"}
_SAFE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{1,63}$")
_BLOCKED_PATH_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}
_BLOCKED_FILENAMES = {".env"}


def code_execute(arguments: dict[str, object]) -> ToolExecutionResult:
    """코드를 별도 프로세스의 임시 작업공간에서 실행하고 판정 정보를 반환합니다."""

    code = str(arguments.get("code") or "")
    lang = str(arguments.get("lang") or "python").lower()
    if not code:
        return ToolExecutionResult(success=False, output="", error="code 인자가 필요합니다.")
    if lang not in _SUPPORTED_CODE_LANGS:
        return ToolExecutionResult(
            success=False,
            output={"supported_langs": sorted(_SUPPORTED_CODE_LANGS)},
            error=f"지원하지 않는 코드 언어입니다: {lang}",
        )

    timeout_seconds = _coerce_timeout(arguments.get("timeout_seconds"))
    with tempfile.TemporaryDirectory(prefix="adaptive-agent-code-") as temp_dir:
        script_path = Path(temp_dir) / "snippet.py"
        script_path.write_text(code, encoding="utf-8")
        command = [sys.executable, "-I", str(script_path)]
        output = _run_subprocess(command, cwd=Path(temp_dir), timeout_seconds=timeout_seconds)
    return _result_from_process(output, arguments)


def shell_run(arguments: dict[str, object]) -> ToolExecutionResult:
    """셸 명령을 별도 프로세스의 임시 작업공간에서 실행하고 판정 정보를 반환합니다."""

    code = str(arguments.get("code") or arguments.get("command") or "")
    lang = str(arguments.get("lang") or "bash").lower()
    if not code:
        return ToolExecutionResult(success=False, output="", error="code 또는 command 인자가 필요합니다.")
    if lang not in _SUPPORTED_SHELL_LANGS:
        return ToolExecutionResult(
            success=False,
            output={"supported_langs": sorted(_SUPPORTED_SHELL_LANGS)},
            error=f"지원하지 않는 셸 언어입니다: {lang}",
        )

    timeout_seconds = _coerce_timeout(arguments.get("timeout_seconds"))
    shell_binary = "/bin/bash" if lang in {"bash", "shell"} else "/bin/sh"
    with tempfile.TemporaryDirectory(prefix="adaptive-agent-shell-") as temp_dir:
        command = [shell_binary, "-c", code]
        output = _run_subprocess(command, cwd=Path(temp_dir), timeout_seconds=timeout_seconds)
    return _result_from_process(output, arguments)


def file_read(arguments: dict[str, object], *, workspace: Path) -> ToolExecutionResult:
    """워크스페이스 내부 파일을 UTF-8 텍스트로 읽습니다."""

    raw_path = str(arguments.get("path") or "")
    resolved = _resolve_workspace_path(workspace, raw_path)
    if isinstance(resolved, ToolExecutionResult):
        return resolved
    if not resolved.exists():
        return ToolExecutionResult(success=False, output="", error=f"파일을 찾을 수 없습니다: {raw_path}")
    if not resolved.is_file():
        return ToolExecutionResult(success=False, output="", error=f"파일이 아닙니다: {raw_path}")
    try:
        return ToolExecutionResult(
            success=True,
            output={
                "path": str(resolved.relative_to(workspace)),
                "content": resolved.read_text(encoding="utf-8"),
            },
        )
    except UnicodeDecodeError as exc:
        return ToolExecutionResult(success=False, output="", error=f"UTF-8 텍스트로 읽을 수 없습니다: {exc}")


def file_write(arguments: dict[str, object], *, workspace: Path) -> ToolExecutionResult:
    """워크스페이스 내부 파일에 UTF-8 텍스트를 씁니다."""

    raw_path = str(arguments.get("path") or "")
    content = arguments.get("content", arguments.get("context"))
    if content is None:
        return ToolExecutionResult(success=False, output="", error="content 또는 context 인자가 필요합니다.")

    resolved = _resolve_workspace_path(workspace, raw_path)
    if isinstance(resolved, ToolExecutionResult):
        return resolved
    if _is_blocked_path(resolved, workspace):
        return ToolExecutionResult(success=False, output="", error="민감한 경로에는 쓸 수 없습니다.")

    if resolved.exists() and resolved.is_dir():
        return ToolExecutionResult(success=False, output="", error=f"디렉터리에는 쓸 수 없습니다: {raw_path}")

    overwrite = _coerce_bool(arguments.get("overwrite"), default=True)
    if resolved.exists() and not overwrite:
        return ToolExecutionResult(success=False, output="", error=f"파일이 이미 있습니다: {raw_path}")
    existed_before = resolved.exists()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(str(content), encoding="utf-8")
    return ToolExecutionResult(
        success=True,
        output={
            "path": str(resolved.relative_to(workspace)),
            "bytes_written": len(str(content).encode("utf-8")),
            "overwritten": existed_before,
        },
    )


def ask_human(arguments: dict[str, object]) -> ToolExecutionResult:
    """사용자 질문/선택 요청을 에이전트가 멈춰 처리할 수 있게 구조화합니다."""

    questions = arguments.get("questions")
    if isinstance(questions, str):
        normalized_questions: list[str] = [questions]
    elif isinstance(questions, list):
        normalized_questions = [str(question) for question in questions]
    else:
        return ToolExecutionResult(success=False, output="", error="questions는 문자열 또는 문자열 배열이어야 합니다.")

    options = arguments.get("options", [])
    normalized_options = [str(option) for option in options] if isinstance(options, list) else []
    return ToolExecutionResult(
        success=True,
        output={
            "status": "pending_human_input",
            "questions": normalized_questions,
            "options": normalized_options,
        },
    )


def propose_actions(arguments: dict[str, object]) -> ToolExecutionResult:
    """실행 전 승인 요청을 구조화해 반환합니다."""

    plan = arguments.get("plan")
    if plan is None:
        return ToolExecutionResult(success=False, output="", error="plan 인자가 필요합니다.")
    risk_level = str(arguments.get("risk_level") or "medium")
    if risk_level not in {"low", "medium", "high"}:
        return ToolExecutionResult(success=False, output="", error="risk_level은 low, medium, high 중 하나여야 합니다.")
    return ToolExecutionResult(
        success=True,
        output={
            "status": "approval_required",
            "plan": plan,
            "risk_level": risk_level,
            "approved": False,
        },
    )


def tool_create(arguments: dict[str, object], *, tool_library: Path) -> ToolExecutionResult:
    """새 툴 코드를 툴 라이브러리에 저장합니다. 코드는 저장 전 문법만 검증합니다."""

    name = str(arguments.get("name") or "")
    description = str(arguments.get("description") or "")
    code = str(arguments.get("code") or "")
    if not _SAFE_NAME_PATTERN.match(name):
        return ToolExecutionResult(success=False, output="", error="name은 영문/숫자/밑줄 2~64자여야 합니다.")
    if not description:
        return ToolExecutionResult(success=False, output="", error="description 인자가 필요합니다.")
    if not code:
        return ToolExecutionResult(success=False, output="", error="code 인자가 필요합니다.")

    try:
        ast.parse(code)
    except SyntaxError as exc:
        return ToolExecutionResult(success=False, output="", error=f"Python 문법 오류: {exc}")

    tool_library.mkdir(parents=True, exist_ok=True)
    code_path = tool_library / f"{name}.py"
    metadata_path = tool_library / f"{name}.json"
    if code_path.exists() and not _coerce_bool(arguments.get("overwrite"), default=False):
        return ToolExecutionResult(success=False, output="", error=f"이미 생성된 툴입니다: {name}")

    code_path.write_text(code, encoding="utf-8")
    metadata = {
        "name": name,
        "description": description,
        "path": str(code_path),
        "status": "created_unloaded",
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return ToolExecutionResult(success=True, output=metadata)


def tool_search(
    arguments: dict[str, object],
    *,
    registered_tools: list[dict[str, Any]],
    tool_library: Path,
) -> ToolExecutionResult:
    """등록 툴과 생성 툴 메타데이터를 이름/설명 기준으로 검색합니다."""

    query = str(arguments.get("query") or "").casefold()
    generated_tools = _load_generated_tool_metadata(tool_library)
    candidates = registered_tools + generated_tools
    if query:
        candidates = [
            tool
            for tool in candidates
            if query in str(tool.get("name", "")).casefold()
            or query in str(tool.get("description", "")).casefold()
            or query in str(tool.get("category", "")).casefold()
        ]
    return ToolExecutionResult(success=True, output={"query": query, "matches": candidates})


def suggested_builtin_tools(_arguments: dict[str, object]) -> ToolExecutionResult:
    """현재 내장 툴 외에 다음 단계에서 유용한 후보를 반환합니다."""

    return ToolExecutionResult(
        success=True,
        output=[
            {
                "name": "file_list",
                "reason": "워크스페이스 탐색을 file_read와 분리하면 LLM이 경로 후보를 안전하게 좁힐 수 있습니다.",
            },
            {
                "name": "file_patch",
                "reason": "전체 파일 덮어쓰기보다 작은 diff 적용을 제공하면 변경 위험을 줄일 수 있습니다.",
            },
            {
                "name": "test_run",
                "reason": "프로젝트별 테스트 명령을 표준 결과와 함께 실행하면 self-correction 루프가 단순해집니다.",
            },
            {
                "name": "tool_validate",
                "reason": "tool_create 이후 문법뿐 아니라 샌드박스 실행과 샘플 입력 검증을 별도 단계로 둘 수 있습니다.",
            },
            {
                "name": "memory_read_write",
                "reason": "사용자 승인 후 반복 작업 선호나 툴 사용 히스토리를 저장하는 계층이 필요합니다.",
            },
        ],
    )


def _run_subprocess(command: list[str], *, cwd: Path, timeout_seconds: float) -> dict[str, object]:
    start = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=_safe_environment(cwd),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = 124
        stdout = _decode_timeout_output(exc.stdout)
        stderr = _decode_timeout_output(exc.stderr) or f"Timed out after {timeout_seconds:g}s"
    duration_ms = int((time.monotonic() - start) * 1000)
    return {
        "command": shlex.join(command),
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": duration_ms,
        "timed_out": timed_out,
        "sandbox": {
            "process_isolated": True,
            "working_directory": "temporary",
            "environment": "minimal",
            "filesystem_isolation": "temporary_cwd_only",
        },
    }


def _result_from_process(process_output: dict[str, object], arguments: dict[str, object]) -> ToolExecutionResult:
    expectation = _evaluate_expectations(process_output, arguments)
    process_success = process_output["exit_code"] == 0 and not bool(process_output["timed_out"])
    success = process_success and expectation["matches_expectation"]
    output = {"execution": process_output, "verdict": expectation}
    error = None if success else _build_execution_error(process_output, expectation)
    return ToolExecutionResult(success=success, output=output, error=error)


def _evaluate_expectations(process_output: dict[str, object], arguments: dict[str, object]) -> dict[str, object]:
    expected_exit_code = int(arguments.get("expected_exit_code", 0))
    stdout = str(process_output.get("stdout") or "")
    stderr = str(process_output.get("stderr") or "")
    checks: dict[str, bool] = {
        "exit_code": process_output.get("exit_code") == expected_exit_code,
    }

    expected_output = arguments.get("expected_output")
    if expected_output is not None:
        checks["stdout_contains_expected_output"] = str(expected_output) in stdout
    expected_stdout = arguments.get("expected_stdout_contains")
    if expected_stdout is not None:
        checks["stdout_contains"] = str(expected_stdout) in stdout
    expected_stderr = arguments.get("expected_stderr_contains")
    if expected_stderr is not None:
        checks["stderr_contains"] = str(expected_stderr) in stderr
    expected_regex = arguments.get("expected_regex")
    if expected_regex is not None:
        checks["stdout_matches_regex"] = re.search(str(expected_regex), stdout) is not None

    return {
        "expected_exit_code": expected_exit_code,
        "checks": checks,
        "matches_expectation": all(checks.values()),
    }


def _build_execution_error(process_output: dict[str, object], expectation: dict[str, object]) -> str:
    if bool(process_output["timed_out"]):
        return "프로세스 실행 시간이 초과되었습니다."
    if process_output["exit_code"] != expectation["expected_exit_code"]:
        return f"프로세스 종료 코드가 기대값과 다릅니다: {process_output['exit_code']}"
    return "프로세스는 실행되었지만 기대 결과 검증에 실패했습니다."


def _safe_environment(temp_dir: Path) -> dict[str, str]:
    path = os.environ.get("PATH", "/usr/bin:/bin")
    return {
        "PATH": path,
        "HOME": str(temp_dir),
        "TMPDIR": str(temp_dir),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
    }


def _coerce_timeout(raw_timeout: object) -> float:
    if raw_timeout is None:
        return 5.0
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError):
        return 5.0
    return min(max(timeout, 0.1), 30.0)


def _coerce_bool(raw_value: object, *, default: bool) -> bool:
    if raw_value is None:
        return default
    if isinstance(raw_value, bool):
        return raw_value
    return str(raw_value).lower() in {"1", "true", "yes", "y", "on"}


def _resolve_workspace_path(workspace: Path, raw_path: str) -> Path | ToolExecutionResult:
    if not raw_path:
        return ToolExecutionResult(success=False, output="", error="path 인자가 필요합니다.")
    workspace = workspace.resolve()
    candidate = (workspace / raw_path).resolve()
    if candidate != workspace and workspace not in candidate.parents:
        return ToolExecutionResult(success=False, output="", error="Workspace 밖의 경로에는 접근할 수 없습니다.")
    return candidate


def _is_blocked_path(path: Path, workspace: Path) -> bool:
    try:
        relative = path.relative_to(workspace)
    except ValueError:
        return True
    if any(part in _BLOCKED_PATH_PARTS for part in relative.parts):
        return True
    return path.name in _BLOCKED_FILENAMES


def _load_generated_tool_metadata(tool_library: Path) -> list[dict[str, Any]]:
    if not tool_library.exists():
        return []
    tools: list[dict[str, Any]] = []
    for metadata_path in sorted(tool_library.glob("*.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(metadata, dict):
            metadata.setdefault("category", "generated")
            metadata.setdefault("safety_level", "unknown")
            tools.append(metadata)
    return tools


def _decode_timeout_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
