"""AdaptiveAgent built-in tool implementations."""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from adaptive_agent.skills import MANIFEST_FILENAME, SkillCatalog
from adaptive_agent.tools.models import ToolExecutionResult
from adaptive_agent.tools.sandbox import LocalSandboxBackend, SandboxPolicyViolation

_SUPPORTED_CODE_LANGS = {"python", "py"}
_SUPPORTED_SHELL_LANGS = {"bash", "sh", "shell"}
_SAFE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{1,63}$")
_BLOCKED_PATH_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}
_BLOCKED_FILENAMES = {".env"}


def code_execute(arguments: dict[str, object], *, sandbox: LocalSandboxBackend) -> ToolExecutionResult:
    """Execute Python code in an isolated temporary workspace."""

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
    try:
        output = sandbox.run_python_code(code, timeout_seconds=timeout_seconds)
    except SandboxPolicyViolation as exc:
        return _policy_violation_result(exc)
    return _result_from_process(output, arguments)


def shell_run(arguments: dict[str, object], *, sandbox: LocalSandboxBackend) -> ToolExecutionResult:
    """Execute shell code in an isolated temporary workspace."""

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
    try:
        output = sandbox.run_shell(code, shell_binary=shell_binary, timeout_seconds=timeout_seconds)
    except SandboxPolicyViolation as exc:
        return _policy_violation_result(exc)
    return _result_from_process(output, arguments)


def file_read(arguments: dict[str, object], *, workspace: Path) -> ToolExecutionResult:
    """Read a UTF-8 text file inside the workspace."""

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
    """Write UTF-8 text to a file inside the workspace."""

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


def file_list(arguments: dict[str, object], *, workspace: Path) -> ToolExecutionResult:
    """Return structured file entries inside the workspace."""

    raw_path = str(arguments.get("path") or ".")
    pattern = str(arguments.get("pattern") or "*")
    recursive = _coerce_bool(arguments.get("recursive"), default=False)
    max_entries = _coerce_int(arguments.get("max_entries"), default=200, minimum=1, maximum=1000)
    resolved = _resolve_workspace_path(workspace, raw_path)
    if isinstance(resolved, ToolExecutionResult):
        return resolved
    if not resolved.exists():
        return ToolExecutionResult(success=False, output="", error=f"경로를 찾을 수 없습니다: {raw_path}")

    if resolved.is_file():
        entries = [_file_entry(resolved, workspace)]
    else:
        iterator = resolved.rglob(pattern) if recursive else resolved.glob(pattern)
        entries = [
            _file_entry(path, workspace)
            for path in sorted(iterator)
            if path != resolved and not _is_blocked_path(path, workspace)
        ]
    truncated = len(entries) > max_entries
    return ToolExecutionResult(
        success=True,
        output={
            "path": str(resolved.relative_to(workspace)) if resolved != workspace else ".",
            "pattern": pattern,
            "recursive": recursive,
            "entries": entries[:max_entries],
            "truncated": truncated,
        },
    )


def file_patch(arguments: dict[str, object], *, workspace: Path) -> ToolExecutionResult:
    """Replace text in one UTF-8 workspace file."""

    raw_path = str(arguments.get("path") or "")
    old_text = arguments.get("old_text")
    new_text = arguments.get("new_text")
    if old_text is None or new_text is None:
        return ToolExecutionResult(success=False, output="", error="old_text와 new_text 인자가 필요합니다.")

    resolved = _resolve_workspace_path(workspace, raw_path)
    if isinstance(resolved, ToolExecutionResult):
        return resolved
    if _is_blocked_path(resolved, workspace):
        return ToolExecutionResult(success=False, output="", error="민감한 경로에는 패치를 적용할 수 없습니다.")
    if not resolved.is_file():
        return ToolExecutionResult(success=False, output="", error=f"파일이 아닙니다: {raw_path}")

    content = resolved.read_text(encoding="utf-8")
    occurrence_count = content.count(str(old_text))
    if occurrence_count == 0:
        return ToolExecutionResult(success=False, output="", error="old_text가 파일에서 발견되지 않았습니다.")
    if occurrence_count > 1 and not _coerce_bool(arguments.get("replace_all"), default=False):
        return ToolExecutionResult(success=False, output="", error="old_text가 여러 번 발견되었습니다. replace_all=true가 필요합니다.")

    replacement_count = occurrence_count if _coerce_bool(arguments.get("replace_all"), default=False) else 1
    updated = content.replace(str(old_text), str(new_text), replacement_count)
    preview = _unified_diff_preview(content, updated, raw_path)
    if _coerce_bool(arguments.get("dry_run"), default=False):
        return ToolExecutionResult(
            success=True,
            output={
                "path": str(resolved.relative_to(workspace)),
                "dry_run": True,
                "replacements": replacement_count,
                "diff": preview,
            },
        )

    resolved.write_text(updated, encoding="utf-8")
    return ToolExecutionResult(
        success=True,
        output={
            "path": str(resolved.relative_to(workspace)),
            "dry_run": False,
            "replacements": replacement_count,
            "diff": preview,
        },
    )


def ask_human(arguments: dict[str, object]) -> ToolExecutionResult:
    """Represent a pending human input request."""

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
    """Represent a pending approval request before execution."""

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


def test_run(arguments: dict[str, object], *, sandbox: LocalSandboxBackend) -> ToolExecutionResult:
    """Run a project test command in an isolated workspace copy."""

    command = str(arguments.get("command") or "python3 -m unittest discover")
    timeout_seconds = _coerce_timeout(arguments.get("timeout_seconds"), default=60.0, maximum=300.0)
    try:
        output = sandbox.run_workspace_command(command, timeout_seconds=timeout_seconds)
    except SandboxPolicyViolation as exc:
        return _policy_violation_result(exc)
    return _result_from_process(output, arguments)


def tool_create(arguments: dict[str, object], *, tool_library: Path) -> ToolExecutionResult:
    """Create generated-tool source and metadata without manifest registration."""

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
        "file_path": str(code_path),
        "file_hash": _sha256_text(code),
        "status": "created_unloaded",
        "validation_status": "created_unloaded",
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return ToolExecutionResult(success=True, output=metadata)


def tool_validate(
    arguments: dict[str, object],
    *,
    tool_library: Path,
    sandbox: LocalSandboxBackend,
) -> ToolExecutionResult:
    """Validate generated Python tool syntax and sample execution."""

    name = str(arguments.get("name") or "")
    if not _SAFE_NAME_PATTERN.match(name):
        return ToolExecutionResult(success=False, output="", error="name은 영문/숫자/밑줄 2~64자여야 합니다.")

    code_path = tool_library / f"{name}.py"
    if not code_path.exists():
        return ToolExecutionResult(success=False, output="", error=f"생성된 툴을 찾을 수 없습니다: {name}")

    code = code_path.read_text(encoding="utf-8")
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return ToolExecutionResult(success=False, output="", error=f"Python 문법 오류: {exc}")

    sample_arguments = arguments.get("sample_arguments", {})
    runner = (
        "import json\n"
        f"generated_code = {code!r}\n"
        "namespace = {}\n"
        "exec(compile(generated_code, '<generated_tool>', 'exec'), namespace)\n"
        "if 'run' not in namespace:\n"
        "    raise AttributeError('generated tool must define run(arguments)')\n"
        f"result = namespace['run']({sample_arguments!r})\n"
        "print(json.dumps(result, ensure_ascii=False, sort_keys=True))\n"
    )
    try:
        process_output = sandbox.run_python_code(
            runner,
            timeout_seconds=_coerce_timeout(arguments.get("timeout_seconds")),
        )
    except SandboxPolicyViolation as exc:
        return _policy_violation_result(exc)
    result = _result_from_process(process_output, arguments)
    if result.success:
        metadata_path = tool_library / f"{name}.json"
        metadata = _read_json_object(metadata_path)
        metadata.update(
            {
                "status": "validated",
                "validated": True,
                "validation_status": "passed",
                "file_hash": _sha256_text(code),
            }
        )
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        result.output["tool"] = metadata
    return result


def generated_tool_execute(
    arguments: dict[str, object],
    *,
    name: str,
    code_path: Path,
    sandbox: LocalSandboxBackend,
) -> ToolExecutionResult:
    """Execute an approved generated tool in a subprocess sandbox."""

    if not code_path.exists():
        return ToolExecutionResult(success=False, output="", error=f"생성 툴 파일을 찾을 수 없습니다: {name}")
    code = code_path.read_text(encoding="utf-8")
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return ToolExecutionResult(success=False, output="", error=f"Python 문법 오류: {exc}")

    runner = (
        "import json\n"
        f"generated_code = {code!r}\n"
        "namespace = {}\n"
        "exec(compile(generated_code, '<generated_tool>', 'exec'), namespace)\n"
        "if 'run' not in namespace:\n"
        "    raise AttributeError('generated tool must define run(arguments)')\n"
        f"result = namespace['run']({arguments!r})\n"
        "print(json.dumps(result, ensure_ascii=False, sort_keys=True))\n"
    )
    try:
        process_output = sandbox.run_python_code(
            runner,
            timeout_seconds=_coerce_timeout(arguments.get("timeout_seconds")),
        )
    except SandboxPolicyViolation as exc:
        return _policy_violation_result(exc)
    result = _result_from_process(process_output, arguments)
    if result.success:
        try:
            result.output["result"] = json.loads(str(result.output["execution"]["stdout"] or "null"))
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
    return result


def tool_approve(arguments: dict[str, object], *, tool_library: Path) -> ToolExecutionResult:
    """Register a validated generated tool in the manifest catalog."""

    name = str(arguments.get("name") or "")
    if not _SAFE_NAME_PATTERN.match(name):
        return ToolExecutionResult(success=False, output="", error="name은 영문/숫자/밑줄 2~64자여야 합니다.")

    metadata_path = tool_library / f"{name}.json"
    code_path = tool_library / f"{name}.py"
    if not metadata_path.exists() or not code_path.exists():
        return ToolExecutionResult(success=False, output="", error=f"승인할 생성 툴을 찾을 수 없습니다: {name}")

    metadata = _read_json_object(metadata_path)
    if metadata.get("validation_status") != "passed":
        return ToolExecutionResult(success=False, output=metadata, error="검증을 통과한 툴만 승인 등록할 수 있습니다.")
    expected_hash = str(metadata.get("file_hash") or "")
    if not expected_hash:
        return ToolExecutionResult(success=False, output=metadata, error="검증된 파일 hash가 없어 승인할 수 없습니다.")
    current_hash = _sha256_text(code_path.read_text(encoding="utf-8"))
    if current_hash != expected_hash:
        return ToolExecutionResult(success=False, output=metadata, error="검증 이후 생성 툴 파일이 변경되어 승인할 수 없습니다.")

    metadata.update({"status": "approved", "approved": True, "approval_status": "approved"})
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    catalog_diff = SkillCatalog(tool_library).upsert_with_diff(metadata)
    return ToolExecutionResult(
        success=True,
        output={
            "tool": metadata,
            "catalog": catalog_diff["entry"],
            "manifest_merge": {
                "merged": catalog_diff["merged"],
                "previous_usage_count": catalog_diff["previous_usage_count"],
                "previous_failure_count": catalog_diff["previous_failure_count"],
            },
        },
    )


def tool_search(
    arguments: dict[str, object],
    *,
    registered_tools: list[dict[str, Any]],
    tool_library: Path,
) -> ToolExecutionResult:
    """Search registered and approved generated-tool metadata."""

    query = str(arguments.get("query") or "").casefold()
    top_k = _coerce_int(arguments.get("top_k"), default=10, minimum=1, maximum=50)
    generated_tools = SkillCatalog(tool_library).search(query, top_k=top_k)
    candidates = _dedupe_tool_candidates(registered_tools + generated_tools)
    if query:
        candidates = [
            tool
            for tool in candidates
            if query in str(tool.get("name", "")).casefold()
            or query in str(tool.get("description", "")).casefold()
            or query in str(tool.get("category", "")).casefold()
            or float(tool.get("score", 0) or 0) > 0
        ]
    return ToolExecutionResult(success=True, output={"query": query, "top_k": top_k, "matches": candidates[:top_k]})


def memory_read(arguments: dict[str, object], *, memory_dir: Path) -> ToolExecutionResult:
    """Read a JSON value from local agent memory."""

    key = str(arguments.get("key") or "")
    resolved = _resolve_memory_path(memory_dir, key)
    if isinstance(resolved, ToolExecutionResult):
        return resolved
    if not resolved.exists():
        return ToolExecutionResult(success=False, output="", error=f"메모리를 찾을 수 없습니다: {key}")
    return ToolExecutionResult(success=True, output=_read_json_object(resolved))


def memory_write(arguments: dict[str, object], *, memory_dir: Path) -> ToolExecutionResult:
    """Write a JSON value to local agent memory."""

    key = str(arguments.get("key") or "")
    value = arguments.get("value")
    if value is None:
        return ToolExecutionResult(success=False, output="", error="value 인자가 필요합니다.")
    resolved = _resolve_memory_path(memory_dir, key)
    if isinstance(resolved, ToolExecutionResult):
        return resolved
    memory_dir.mkdir(parents=True, exist_ok=True)
    payload = {"key": key, "value": value}
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return ToolExecutionResult(success=True, output=payload)


def suggested_builtin_tools(_arguments: dict[str, object]) -> ToolExecutionResult:
    """Return candidate builtin tools for future core expansion."""

    # artifact_store / web_fetch는 이제 실제 핸들러로 등록되었지만, 후보
    # 목록은 향후 확장 가이드를 위해 유지한다 (artifact_search, mcp_bridge 등).
    return ToolExecutionResult(
        success=True,
        output=[
            {
                "name": "artifact_search",
                "reason": "저장된 artifact를 query/태그로 검색하는 보조 도구가 필요합니다.",
            },
            {
                "name": "mcp_bridge",
                "reason": "외부 MCP 서버의 도구를 동적 등록할 수 있는 어댑터가 필요합니다.",
            },
        ],
    )


_ARTIFACT_OPS = {"put", "get", "list", "delete"}
_ARTIFACT_DIRNAME = ".adaptive_agent/artifacts"


def artifact_store(
    arguments: dict[str, object],
    *,
    workspace: Path,
    max_bytes: int = 10 * 1024 * 1024,
    max_count: int = 1000,
) -> ToolExecutionResult:
    """Persist binary/text artifacts under the workspace with sha256 IDs.

    Supported ops:

    - ``put``: write bytes (or base64-encoded ``content_base64`` /
      utf8 ``content``) and return ``{artifact_id, sha256, bytes_written, path}``.
    - ``get``: read by ``artifact_id``; returns ``{artifact_id, mime_type,
      content_base64, bytes}``.
    - ``list``: enumerate stored artifacts with metadata.
    - ``delete``: remove by ``artifact_id``.

    Each artifact is stored as ``<workspace>/.adaptive_agent/artifacts/<sha256>.bin``
    plus a sibling ``.json`` with ``{name, mime_type, sha256, bytes, created_at}``.
    """

    op = str(arguments.get("op") or "put").lower()
    if op not in _ARTIFACT_OPS:
        return ToolExecutionResult(success=False, output="", error=f"지원하지 않는 op입니다: {op} (지원: {sorted(_ARTIFACT_OPS)})")

    artifacts_dir = (workspace / _ARTIFACT_DIRNAME).resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    if op == "put":
        return _artifact_put(arguments, artifacts_dir=artifacts_dir, max_bytes=max_bytes, max_count=max_count)
    if op == "get":
        return _artifact_get(arguments, artifacts_dir=artifacts_dir)
    if op == "list":
        return _artifact_list(artifacts_dir=artifacts_dir, prefix=str(arguments.get("prefix") or ""))
    return _artifact_delete(arguments, artifacts_dir=artifacts_dir)


def _artifact_put(
    arguments: dict[str, object],
    *,
    artifacts_dir: Path,
    max_bytes: int,
    max_count: int,
) -> ToolExecutionResult:
    import base64

    name = str(arguments.get("name") or "")
    if not name or "/" in name or ".." in name:
        return ToolExecutionResult(success=False, output="", error="name은 디렉터리 구분자/.. 없는 단일 토큰이어야 합니다.")
    mime_type = str(arguments.get("mime_type") or "application/octet-stream")

    raw_content = arguments.get("content")
    raw_b64 = arguments.get("content_base64")
    if raw_b64 is not None:
        try:
            payload = base64.b64decode(str(raw_b64), validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            return ToolExecutionResult(success=False, output="", error=f"content_base64 디코드 실패: {exc}")
    elif isinstance(raw_content, (str, bytes)):
        payload = raw_content.encode("utf-8") if isinstance(raw_content, str) else raw_content
    else:
        return ToolExecutionResult(success=False, output="", error="content 또는 content_base64 인자가 필요합니다.")

    if len(payload) > max_bytes:
        return ToolExecutionResult(
            success=False,
            output={"max_bytes": max_bytes, "actual_bytes": len(payload)},
            error=f"artifact 크기가 한도를 초과했습니다: {len(payload)} > {max_bytes}",
        )

    existing = list(artifacts_dir.glob("*.bin"))
    if len(existing) >= max_count:
        return ToolExecutionResult(
            success=False,
            output={"max_count": max_count, "current_count": len(existing)},
            error=f"artifact 개수 한도를 초과했습니다: {len(existing)} >= {max_count}",
        )

    sha = hashlib.sha256(payload).hexdigest()
    bin_path = artifacts_dir / f"{sha}.bin"
    meta_path = artifacts_dir / f"{sha}.json"
    bin_path.write_bytes(payload)
    metadata = {
        "name": name,
        "mime_type": mime_type,
        "sha256": sha,
        "bytes": len(payload),
        "created_at": _utc_now_iso(),
    }
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    return ToolExecutionResult(
        success=True,
        output={
            "artifact_id": sha,
            "sha256": sha,
            "bytes_written": len(payload),
            "path": str(bin_path),
            "name": name,
        },
    )


def _artifact_get(arguments: dict[str, object], *, artifacts_dir: Path) -> ToolExecutionResult:
    import base64

    artifact_id = str(arguments.get("artifact_id") or "")
    if not re.fullmatch(r"[a-f0-9]{64}", artifact_id):
        return ToolExecutionResult(success=False, output="", error="artifact_id는 64자 hex여야 합니다.")
    bin_path = artifacts_dir / f"{artifact_id}.bin"
    meta_path = artifacts_dir / f"{artifact_id}.json"
    if not bin_path.exists() or not meta_path.exists():
        return ToolExecutionResult(success=False, output="", error=f"artifact를 찾을 수 없습니다: {artifact_id}")
    metadata = _read_json_object(meta_path)
    payload = bin_path.read_bytes()
    return ToolExecutionResult(
        success=True,
        output={
            "artifact_id": artifact_id,
            "name": metadata.get("name", ""),
            "mime_type": metadata.get("mime_type", "application/octet-stream"),
            "bytes": len(payload),
            "content_base64": base64.b64encode(payload).decode("ascii"),
        },
    )


def _artifact_list(*, artifacts_dir: Path, prefix: str) -> ToolExecutionResult:
    entries: list[dict[str, object]] = []
    for meta_path in sorted(artifacts_dir.glob("*.json")):
        metadata = _read_json_object(meta_path)
        if prefix and not str(metadata.get("name", "")).startswith(prefix):
            continue
        entries.append(
            {
                "artifact_id": meta_path.stem,
                "name": metadata.get("name", ""),
                "mime_type": metadata.get("mime_type", ""),
                "bytes": metadata.get("bytes", 0),
                "created_at": metadata.get("created_at", ""),
            }
        )
    return ToolExecutionResult(success=True, output={"entries": entries, "count": len(entries)})


def _artifact_delete(arguments: dict[str, object], *, artifacts_dir: Path) -> ToolExecutionResult:
    artifact_id = str(arguments.get("artifact_id") or "")
    if not re.fullmatch(r"[a-f0-9]{64}", artifact_id):
        return ToolExecutionResult(success=False, output="", error="artifact_id는 64자 hex여야 합니다.")
    bin_path = artifacts_dir / f"{artifact_id}.bin"
    meta_path = artifacts_dir / f"{artifact_id}.json"
    deleted = False
    if bin_path.exists():
        bin_path.unlink()
        deleted = True
    if meta_path.exists():
        meta_path.unlink()
        deleted = True
    if not deleted:
        return ToolExecutionResult(success=False, output="", error=f"삭제할 artifact를 찾을 수 없습니다: {artifact_id}")
    return ToolExecutionResult(success=True, output={"artifact_id": artifact_id, "deleted": True})


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def web_fetch(
    arguments: dict[str, object],
    *,
    allowed_domains: list[str] | None = None,
    max_bytes: int = 1024 * 1024,
    timeout_seconds: float = 10.0,
) -> ToolExecutionResult:
    """Fetch a URL with a strict domain whitelist (default deny).

    Decision (issue #19): use ``urllib.request`` from stdlib instead of
    httpx so this builtin works without adding a dependency. Behavior is
    equivalent for the supported scope (GET/POST, JSON/text body, custom
    headers, redirect handling, byte cap).

    Block reason ``domain_not_allowlisted`` is surfaced in the verdict for
    machine-readable rejection (matches the policy enum spirit of #21).
    """

    import time as _time
    import urllib.error
    import urllib.parse
    import urllib.request

    url = str(arguments.get("url") or "")
    if not url:
        return ToolExecutionResult(success=False, output="", error="url 인자가 필요합니다.")

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ToolExecutionResult(success=False, output="", error=f"지원하지 않는 scheme: {parsed.scheme}")
    host = (parsed.hostname or "").lower()
    if not host:
        return ToolExecutionResult(success=False, output="", error="URL에서 host를 추출할 수 없습니다.")

    allowed = [d.strip().lower() for d in (allowed_domains or []) if d.strip()]
    if not _domain_in_allowlist(host, allowed):
        return ToolExecutionResult(
            success=False,
            output={
                "verdict": {
                    "policy_blocked": True,
                    "block_reason": "domain_not_allowlisted",
                    "host": host,
                    "allowed": allowed,
                }
            },
            error=f"도메인이 화이트리스트에 없습니다: {host}",
        )

    method = str(arguments.get("method") or "GET").upper()
    headers_raw = arguments.get("headers") or {}
    headers = {str(k): str(v) for k, v in headers_raw.items()} if isinstance(headers_raw, dict) else {}
    body_raw = arguments.get("body")
    body_bytes: bytes | None = None
    if body_raw is not None:
        body_bytes = body_raw.encode("utf-8") if isinstance(body_raw, str) else json.dumps(body_raw).encode("utf-8")

    request = urllib.request.Request(url, data=body_bytes, method=method, headers=headers)
    started = _time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read(max_bytes + 1)
            truncated = len(payload) > max_bytes
            payload = payload[:max_bytes]
            elapsed_ms = int((_time.monotonic() - started) * 1000)
            response_headers = {k: v for k, v in response.headers.items()}
            try:
                body_text = payload.decode("utf-8")
                body_truncated_text = truncated
            except UnicodeDecodeError:
                import base64

                body_text = base64.b64encode(payload).decode("ascii")
                body_truncated_text = truncated
            return ToolExecutionResult(
                success=True,
                output={
                    "status_code": response.status,
                    "headers": response_headers,
                    "body_text": body_text,
                    "body_truncated": body_truncated_text,
                    "bytes_read": len(payload),
                    "elapsed_ms": elapsed_ms,
                    "host": host,
                },
            )
    except urllib.error.HTTPError as exc:
        elapsed_ms = int((_time.monotonic() - started) * 1000)
        return ToolExecutionResult(
            success=False,
            output={
                "status_code": exc.code,
                "host": host,
                "elapsed_ms": elapsed_ms,
            },
            error=f"HTTP 오류: {exc.code} {exc.reason}",
        )
    except urllib.error.URLError as exc:
        return ToolExecutionResult(success=False, output={"host": host}, error=f"네트워크 오류: {exc.reason}")
    except (TimeoutError, OSError) as exc:
        return ToolExecutionResult(success=False, output={"host": host}, error=f"네트워크 호출 실패: {exc}")


def _domain_in_allowlist(host: str, allowed: list[str]) -> bool:
    """Return True if host matches any allowed entry (exact or subdomain)."""

    if not allowed:
        return False
    for entry in allowed:
        if not entry:
            continue
        if host == entry or host.endswith("." + entry):
            return True
    return False


def _result_from_process(process_output: dict[str, object], arguments: dict[str, object]) -> ToolExecutionResult:
    expectation = _evaluate_expectations(process_output, arguments)
    process_success = process_output["exit_code"] == 0 and not bool(process_output["timed_out"])
    success = process_success and expectation["matches_expectation"]
    output = {"execution": process_output, "verdict": expectation}
    error = None if success else _build_execution_error(process_output, expectation)
    return ToolExecutionResult(success=success, output=output, error=error)


_KNOWN_BLOCK_REASONS = frozenset(
    {"workspace_path", "sensitive_absolute_path", "dangerous_shell_pattern"}
)


def _policy_violation_result(violation: SandboxPolicyViolation) -> ToolExecutionResult:
    reason = getattr(violation, "reason", None) or "unspecified"
    return ToolExecutionResult(
        success=False,
        output={
            "execution": None,
            "verdict": {
                "matches_expectation": False,
                "policy_blocked": True,
                "block_reason": reason,
            },
        },
        error=str(violation),
    )


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


def _coerce_timeout(raw_timeout: object, *, default: float = 5.0, maximum: float = 30.0) -> float:
    if raw_timeout is None:
        return default
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError):
        return default
    return min(max(timeout, 0.1), maximum)


def _coerce_int(raw_value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    return min(max(value, minimum), maximum)


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


def _file_entry(path: Path, workspace: Path) -> dict[str, object]:
    stat = path.stat()
    return {
        "path": str(path.relative_to(workspace)),
        "type": "directory" if path.is_dir() else "file",
        "size_bytes": stat.st_size if path.is_file() else None,
    }


def _unified_diff_preview(before: str, after: str, path: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{path}:before",
            tofile=f"{path}:after",
        )
    )


def _load_generated_tool_metadata(tool_library: Path) -> list[dict[str, Any]]:
    return SkillCatalog(tool_library).list()


def _dedupe_tool_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for tool in candidates:
        name = str(tool.get("name") or "")
        if not name:
            continue
        existing = deduped.get(name)
        if existing is None:
            deduped[name] = tool
            continue
        if str(existing.get("source") or "builtin") != "builtin" and str(tool.get("source") or "") == "builtin":
            deduped[name] = tool
    return sorted(deduped.values(), key=lambda item: (-float(item.get("score", 1) or 1), str(item.get("name", ""))))


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _resolve_memory_path(memory_dir: Path, key: str) -> Path | ToolExecutionResult:
    if not _SAFE_NAME_PATTERN.match(key):
        return ToolExecutionResult(success=False, output="", error="key는 영문/숫자/밑줄 2~64자여야 합니다.")
    memory_dir = memory_dir.resolve()
    candidate = (memory_dir / f"{key}.json").resolve()
    if memory_dir != candidate.parent:
        return ToolExecutionResult(success=False, output="", error="메모리 경로가 허용 범위를 벗어났습니다.")
    return candidate
