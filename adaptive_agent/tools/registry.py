"""Tool registry and built-in tools."""

from __future__ import annotations

import hashlib
from pathlib import Path

from adaptive_agent.skills import SkillCatalog
from adaptive_agent.tools import builtins
from adaptive_agent.tools.models import Tool, ToolExecutionResult
from adaptive_agent.tools.sandbox import LocalSandboxBackend


class ToolRegistry:
    """In-memory index of executable tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self.generated_load_results: list[dict[str, object]] = []

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def unregister(self, name: str) -> bool:
        """Remove a tool by name. Returns True if removed, False if not found."""

        return self._tools.pop(name, None) is not None


def create_default_registry(
    workspace_dir: Path | None = None,
    tool_library_dir: Path | None = None,
    artifact_dir: Path | None = None,
) -> ToolRegistry:
    """Create the default builtin tool registry."""

    registry = ToolRegistry()
    raw_workspace = workspace_dir or Path.cwd()
    workspace = raw_workspace.resolve()
    tool_library = (tool_library_dir or workspace / ".adaptive_agent" / "tools").resolve()
    memory_dir = workspace / ".adaptive_agent" / "memory"
    _artifact_dir = (artifact_dir or workspace / ".adaptive_agent" / "artifacts").resolve()
    sandbox = LocalSandboxBackend(raw_workspace)

    def echo(arguments: dict[str, object]) -> ToolExecutionResult:
        return ToolExecutionResult(success=True, output=arguments.get("task", ""))

    def analyze_requirements(_arguments: dict[str, object]) -> ToolExecutionResult:
        """Return structured implementation tasks derived from reference.md."""

        return ToolExecutionResult(success=True, output=_requirements_breakdown())

    def list_tools(_arguments: dict[str, object]) -> ToolExecutionResult:
        tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "category": tool.category,
                "safety_level": tool.safety_level,
                "usage": tool.usage,
                "source": tool.source,
            }
            for tool in registry.list()
        ]
        output: object = tools
        if registry.generated_load_results:
            output = {"tools": tools, "generated_load_results": registry.generated_load_results}
        return ToolExecutionResult(success=True, output=output)

    def list_files(arguments: dict[str, object]) -> ToolExecutionResult:
        raw_path = str(arguments.get("path") or ".")
        candidate = (workspace / raw_path).resolve()
        if candidate != workspace and workspace not in candidate.parents:
            return ToolExecutionResult(success=False, output="", error="Workspace 밖의 경로는 조회할 수 없습니다.")
        if not candidate.exists():
            return ToolExecutionResult(success=False, output="", error=f"경로를 찾을 수 없습니다: {raw_path}")
        if candidate.is_file():
            return ToolExecutionResult(success=True, output=str(candidate.relative_to(workspace)))

        entries = sorted(
            str(path.relative_to(workspace)) + ("/" if path.is_dir() else "")
            for path in candidate.iterdir()
            if path.name not in {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}
        )
        return ToolExecutionResult(success=True, output="\n".join(entries))

    registry.register(
        Tool(
            name="echo",
            description="입력 작업을 그대로 반환하는 상태 확인용 툴입니다.",
            handler=echo,
            category="atomic",
            usage='python3 -m adaptive_agent --tool echo --arg task="echo hello"',
        )
    )
    registry.register(
        Tool(
            name="analyze_requirements",
            description="reference.md 방법론을 기반으로 프로젝트 요구사항을 분해합니다.",
            handler=analyze_requirements,
            category="planning",
            usage="python3 -m adaptive_agent --tool analyze_requirements",
        )
    )
    registry.register(
        Tool(
            name="list_tools",
            description="등록된 내장 툴 목록을 출력합니다.",
            handler=list_tools,
            category="utility",
            usage="python3 -m adaptive_agent --list-tools",
        )
    )
    registry.register(
        Tool(
            name="list_files",
            description="작업공간 파일 목록을 안전하게 조회합니다.",
            handler=list_files,
            category="utility",
            usage="python3 -m adaptive_agent --tool list_files --arg path=adaptive_agent",
        )
    )
    registry.register(
        Tool(
            name="code_execute",
            description="Python 코드를 별도 프로세스의 임시 작업공간에서 실행하고 결과/에러/기대값 판정을 반환합니다.",
            handler=lambda arguments: builtins.code_execute(arguments, sandbox=sandbox),
            category="execution",
            safety_level="high",
            usage='python3 -m adaptive_agent --json --tool code_execute --arg code="print(1)" --arg lang=python',
        )
    )
    registry.register(
        Tool(
            name="shell_run",
            description="셸 명령을 별도 프로세스의 임시 작업공간에서 실행하고 결과/에러/기대값 판정을 반환합니다.",
            handler=lambda arguments: builtins.shell_run(arguments, sandbox=sandbox),
            category="execution",
            safety_level="high",
            usage='python3 -m adaptive_agent --json --tool shell_run --arg code="echo ok"',
        )
    )
    registry.register(
        Tool(
            name="file_read",
            description="워크스페이스 내부 UTF-8 텍스트 파일을 읽습니다.",
            handler=lambda arguments: builtins.file_read(arguments, workspace=workspace),
            category="filesystem",
            safety_level="medium",
            usage="python3 -m adaptive_agent --json --tool file_read --arg path=README.md",
        )
    )
    registry.register(
        Tool(
            name="file_write",
            description="워크스페이스 내부 파일에 UTF-8 텍스트를 씁니다.",
            handler=lambda arguments: builtins.file_write(arguments, workspace=workspace),
            category="filesystem",
            safety_level="high",
            usage='python3 -m adaptive_agent --json --tool file_write --arg path=notes.txt --arg content="hello"',
        )
    )
    registry.register(
        Tool(
            name="file_list",
            description="워크스페이스 내부 파일/디렉터리를 구조화된 목록으로 조회합니다.",
            handler=lambda arguments: builtins.file_list(arguments, workspace=workspace),
            category="filesystem",
            safety_level="low",
            usage="python3 -m adaptive_agent --json --tool file_list --arg path=adaptive_agent --arg recursive=true",
        )
    )
    registry.register(
        Tool(
            name="file_patch",
            description="워크스페이스 내부 UTF-8 파일에 단일 텍스트 치환 패치를 적용하거나 diff를 미리 봅니다.",
            handler=lambda arguments: builtins.file_patch(arguments, workspace=workspace),
            category="filesystem",
            safety_level="high",
            usage='python3 -m adaptive_agent --json --tool file_patch --arg path=notes.txt --arg old_text=old --arg new_text=new --arg dry_run=true',
        )
    )
    registry.register(
        Tool(
            name="ask_human",
            description="사용자에게 질문 또는 선택지를 요청하는 pending_human_input 결과를 반환합니다.",
            handler=builtins.ask_human,
            category="human_in_the_loop",
            safety_level="low",
            usage='python3 -m adaptive_agent --json --tool ask_human --arg questions="어떤 옵션을 선택할까요?"',
        )
    )
    registry.register(
        Tool(
            name="propose_actions",
            description="실행 전 계획과 위험도를 제시하고 사용자 승인이 필요함을 반환합니다.",
            handler=builtins.propose_actions,
            category="human_in_the_loop",
            safety_level="low",
            usage='python3 -m adaptive_agent --json --tool propose_actions --arg plan="파일을 수정합니다" --arg risk_level=medium',
        )
    )
    registry.register(
        Tool(
            name="tool_create",
            description="새 Python 도구 코드를 툴 라이브러리에 저장합니다.",
            handler=lambda arguments: builtins.tool_create(arguments, tool_library=tool_library),
            category="tool_library",
            safety_level="high",
            usage='python3 -m adaptive_agent --json --tool tool_create --arg name=my_tool --arg description="..." --arg code="def run(args): return args"',
        )
    )
    registry.register(
        Tool(
            name="tool_search",
            description="등록된 내장 도구와 저장된 생성 도구를 이름/설명 기준으로 검색합니다.",
            handler=lambda arguments: builtins.tool_search(
                arguments,
                registered_tools=[
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "category": tool.category,
                        "safety_level": tool.safety_level,
                        "usage": tool.usage,
                        "source": tool.source,
                    }
                    for tool in registry.list()
                ],
                tool_library=tool_library,
            ),
            category="tool_library",
            safety_level="low",
            usage="python3 -m adaptive_agent --json --tool tool_search --arg query=file",
        )
    )
    registry.register(
        Tool(
            name="tool_validate",
            description="생성된 Python 도구의 문법과 run(arguments) 샘플 실행을 샌드박스에서 검증합니다.",
            handler=lambda arguments: builtins.tool_validate(
                arguments,
                tool_library=tool_library,
                sandbox=sandbox,
            ),
            category="tool_library",
            safety_level="high",
            usage="python3 -m adaptive_agent --json --tool tool_validate --arg name=my_tool",
        )
    )
    registry.register(
        Tool(
            name="tool_approve",
            description="사용자 승인 후 검증된 생성 도구를 manifest 스킬 카탈로그에 등록합니다.",
            handler=lambda arguments: builtins.tool_approve(arguments, tool_library=tool_library),
            category="tool_library",
            safety_level="high",
            usage="python3 -m adaptive_agent --json --tool tool_approve --arg name=my_tool",
        )
    )
    registry.register(
        Tool(
            name="memory_read",
            description="에이전트 로컬 메모리 값을 읽습니다.",
            handler=lambda arguments: builtins.memory_read(arguments, memory_dir=memory_dir),
            category="memory",
            safety_level="medium",
            usage="python3 -m adaptive_agent --json --tool memory_read --arg key=preference",
        )
    )
    registry.register(
        Tool(
            name="memory_write",
            description="사용자 승인 후 유지할 에이전트 로컬 메모리 값을 저장합니다.",
            handler=lambda arguments: builtins.memory_write(arguments, memory_dir=memory_dir),
            category="memory",
            safety_level="high",
            usage='python3 -m adaptive_agent --json --tool memory_write --arg key=preference --arg value="한국어 응답"',
        )
    )
    registry.register(
        Tool(
            name="artifact_store",
            description="실행 로그, diff, 생성 파일을 로컬 artifact 디렉터리에 저장합니다.",
            handler=lambda arguments: builtins.artifact_store(arguments, artifact_dir=_artifact_dir),
            category="io",
            safety_level="medium",
            usage='python3 -m adaptive_agent --json --tool artifact_store --arg name=result.txt --arg content="..."',
        )
    )
    registry.register(
        Tool(
            name="suggest_builtin_tools",
            description="현재 목록 외에 추가로 유용한 내장 도구 후보와 이유를 반환합니다.",
            handler=builtins.suggested_builtin_tools,
            category="planning",
            safety_level="low",
            usage="python3 -m adaptive_agent --tool suggest_builtin_tools",
        )
    )
    registry.register(
        Tool(
            name="skill_list",
            description="저장된 스킬 목록과 메타데이터를 반환합니다.",
            handler=lambda arguments: builtins.skill_list(arguments, tool_library=tool_library),
            category="meta",
            safety_level="low",
            usage='adaptive-agent "저장된 스킬 목록 보여줘"',
        )
    )
    registry.register(
        Tool(
            name="skill_delete",
            description="이름으로 스킬을 삭제합니다. manifest와 파일을 모두 제거합니다.",
            handler=lambda arguments: builtins.skill_delete(
                arguments, tool_library=tool_library, registry=registry
            ),
            category="meta",
            safety_level="high",
            usage='adaptive-agent "compute_stats 스킬 삭제해줘"',
        )
    )
    registry.generated_load_results = load_generated_tools(registry, tool_library=tool_library, sandbox=sandbox)
    return registry


def load_generated_tools(
    registry: ToolRegistry,
    *,
    tool_library: Path,
    sandbox: LocalSandboxBackend,
) -> list[dict[str, object]]:
    """Load approved generated tools from the manifest into the registry."""

    load_results: list[dict[str, object]] = []
    catalog = SkillCatalog(tool_library)
    tool_library = tool_library.resolve()
    for metadata in catalog.list():
        name = str(metadata.get("name") or "")
        if not name:
            continue
        if registry.get(name) is not None:
            load_results.append({"name": name, "loaded": False, "reason": "duplicate_tool_name"})
            continue
        if metadata.get("validation_status") not in {"passed", "structure_only"} or metadata.get("approval_status") != "approved":
            load_results.append({"name": name, "loaded": False, "reason": "not_approved_or_validated"})
            continue
        code_path = _resolve_generated_tool_path(tool_library, metadata)
        if code_path is None or not code_path.exists():
            load_results.append({"name": name, "loaded": False, "reason": "missing_generated_tool_file"})
            continue
        expected_hash = str(metadata.get("file_hash") or "")
        if not expected_hash:
            load_results.append({"name": name, "loaded": False, "reason": "missing_generated_tool_file_hash"})
            continue
        actual_hash = hashlib.sha256(code_path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            load_results.append({"name": name, "loaded": False, "reason": "generated_tool_file_hash_mismatch"})
            continue
        registry.register(
            Tool(
                name=name,
                description=str(metadata.get("description") or "승인된 생성 도구입니다."),
                handler=lambda arguments, tool_name=name, path=code_path: builtins.generated_tool_execute(
                    arguments,
                    name=tool_name,
                    code_path=path,
                    sandbox=sandbox,
                ),
                category=str(metadata.get("category") or "generated"),
                safety_level=str(metadata.get("safety_level") or "high"),
                usage=f"python3 -m adaptive_agent --json --tool {name}",
                source="generated",
                parameters=metadata.get("parameters") if isinstance(metadata.get("parameters"), dict) else {},
                returns=metadata.get("returns") if isinstance(metadata.get("returns"), dict) else {},
                validation_status="passed",
            )
        )
        load_results.append({"name": name, "loaded": True, "reason": "loaded"})
    return load_results


def _resolve_generated_tool_path(tool_library: Path, metadata: dict[str, object]) -> Path | None:
    raw_path = str(metadata.get("file_path") or metadata.get("path") or "")
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = tool_library / raw_path
    resolved = candidate.resolve()
    if resolved != tool_library and tool_library not in resolved.parents:
        return None
    return resolved


def _requirements_breakdown() -> dict[str, object]:
    """Structured requirements breakdown derived from reference.md."""

    return {
        "goal": "CLI 기반 AdaptiveAgent가 자연어 작업을 분석하고 필요한 툴을 생성/검증/재사용한다.",
        "requirements": [
            {
                "id": "R1",
                "name": "작업 분석 및 계획",
                "details": [
                    "사용자 입력 원문을 보존한 채 LLM이 의도를 분류한다.",
                    "LLM 계획을 통해 즉시 실행 가능한 내장 툴, 기존 스킬, 새 툴 생성 필요 여부를 결정한다.",
                    "모호하면 human-in-the-loop 질문을 반환한다.",
                ],
                "reference": "ToolLibGen, SkillX",
            },
            {
                "id": "R2",
                "name": "툴 인터페이스 표준화",
                "details": [
                    "모든 툴은 이름, 설명, 입력 스키마, 실행 핸들러, 안전 등급을 가진다.",
                    "입출력은 JSON 직렬화 가능한 구조를 기본으로 한다.",
                ],
                "reference": "MCP specification",
            },
            {
                "id": "R3",
                "name": "동적 툴 생성",
                "details": [
                    "반복되는 결정론적 작업을 파이썬 함수로 생성한다.",
                    "생성 전 기존 툴과 중복 여부를 확인한다.",
                    "생성된 툴은 독립 프로세스 또는 샌드박스에서 검증한다.",
                ],
                "reference": "ToolMaker, ToolLibGen",
            },
            {
                "id": "R4",
                "name": "툴 검증 및 self-correction",
                "details": [
                    "생성 툴은 단위 테스트, 실행 성공률, 안전성, 재사용성을 확인한다.",
                    "오류 발생 시 제한된 횟수만 수정 루프를 돌고 실패 원인을 분류한다.",
                ],
                "reference": "EvolveTool-Bench, Probabilistic self-correction",
            },
            {
                "id": "R5",
                "name": "스킬 라이브러리 관리",
                "details": [
                    "툴을 계획 스킬, 기능 스킬, 원자 스킬로 분류해 저장한다.",
                    "중복 제거, 버전 관리, 검색 성능 저하 방지를 위한 병합 정책을 둔다.",
                ],
                "reference": "SkillX, AgentEvolver",
            },
            {
                "id": "R6",
                "name": "CLI 실행 검증",
                "details": [
                    "Codespace에서 의존성 설치 후 `python3 -m adaptive_agent`로 실행 가능해야 한다.",
                    "명시적 `--tool` 호출로 요구사항 분석, 툴 목록, echo가 검증 가능해야 한다.",
                    "Ollama 연결 시 자연어 입력에 대한 LLM 계획/응답을 검증한다.",
                ],
                "reference": "Project operation requirement",
            },
        ],
        "milestones": [
            "M1: CLI/설정/내장 툴/요구사항 분석 출력",
            "M2: LLM 기반 계획기와 툴 선택기",
            "M3: 생성 툴 저장소와 검증 파이프라인",
            "M4: self-correction 및 실패 귀인",
            "M5: 스킬 라이브러리 최적화와 MCP 호환 인터페이스",
        ],
    }
