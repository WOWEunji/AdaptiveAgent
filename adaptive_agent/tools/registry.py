"""Tool registry and built-in keyword-matched tools."""

from __future__ import annotations

import json
from pathlib import Path

from adaptive_agent.tools.models import Tool, ToolExecutionResult


class ToolRegistry:
    """실행 가능한 툴을 등록하고 간단한 키워드로 매칭합니다."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def match(self, task: str) -> Tool | None:
        normalized = task.lower()
        for tool in self._tools.values():
            if any(keyword.lower() in normalized for keyword in tool.keywords):
                return tool
        return None

    def list(self) -> list[Tool]:
        return list(self._tools.values())


def create_default_registry(workspace_dir: Path | None = None) -> ToolRegistry:
    """초기 프로젝트에서 사용할 내장 툴을 등록합니다."""

    registry = ToolRegistry()
    workspace = (workspace_dir or Path.cwd()).resolve()

    def echo(arguments: dict[str, object]) -> ToolExecutionResult:
        return ToolExecutionResult(success=True, output=arguments.get("task", ""))

    def extract_path(task: str) -> str:
        """간단한 CLI 검증용으로 작업 문자열에서 경로를 추출합니다."""

        parts = task.split()
        for marker in ("path=", "경로="):
            for part in parts:
                if part.startswith(marker):
                    return part[len(marker) :]
        return "."

    def analyze_requirements(_arguments: dict[str, object]) -> ToolExecutionResult:
        """reference.md 방법론을 구현 과제로 분해한 결과를 반환합니다."""

        return ToolExecutionResult(success=True, output=_requirements_breakdown())

    def list_tools(_arguments: dict[str, object]) -> ToolExecutionResult:
        tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "keywords": list(tool.keywords),
                "category": tool.category,
                "safety_level": tool.safety_level,
                "usage": tool.usage,
            }
            for tool in registry.list()
        ]
        return ToolExecutionResult(success=True, output=json.dumps(tools, ensure_ascii=False, indent=2))

    def list_files(arguments: dict[str, object]) -> ToolExecutionResult:
        raw_path = extract_path(str(arguments.get("task") or ""))
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
            keywords=("echo", "반복", "그대로", "ping"),
            handler=echo,
            category="atomic",
            usage='python3 -m adaptive_agent --json "echo hello"',
        )
    )
    registry.register(
        Tool(
            name="analyze_requirements",
            description="reference.md 방법론을 기반으로 프로젝트 요구사항을 분해합니다.",
            keywords=("요구사항", "요구 사항", "분석", "분해", "방법론", "아키텍처"),
            handler=analyze_requirements,
            category="planning",
            usage='python3 -m adaptive_agent "요구사항 분해 보여줘"',
        )
    )
    registry.register(
        Tool(
            name="list_tools",
            description="등록된 내장 툴 목록을 출력합니다.",
            keywords=("툴 목록", "도구 목록", "list tools", "tools"),
            handler=list_tools,
            category="utility",
            usage="python3 -m adaptive_agent --list-tools",
        )
    )
    registry.register(
        Tool(
            name="list_files",
            description="작업공간 파일 목록을 안전하게 조회합니다.",
            keywords=("파일 목록", "ls", "list files", "디렉터리"),
            handler=list_files,
            category="utility",
            usage='python3 -m adaptive_agent --json "파일 목록 path=adaptive_agent"',
        )
    )
    return registry


def _requirements_breakdown() -> dict[str, object]:
    """reference.md 기반 요구사항 분해 결과를 구조화된 데이터로 제공합니다."""

    return {
        "goal": "CLI 기반 AdaptiveAgent가 자연어 작업을 분석하고 필요한 툴을 생성/검증/재사용한다.",
        "requirements": [
            {
                "id": "R1",
                "name": "작업 분석 및 계획",
                "details": [
                    "사용자 입력을 정규화하고 의도를 분류한다.",
                    "즉시 실행 가능한 내장 툴, 기존 스킬, 새 툴 생성 필요 여부를 결정한다.",
                    "모호하면 human-in-the-loop 질문을 반환한다.",
                ],
                "reference": "ToolLibGen, SkillX",
            },
            {
                "id": "R2",
                "name": "툴 인터페이스 표준화",
                "details": [
                    "모든 툴은 이름, 설명, 키워드, 입력 스키마, 실행 핸들러, 안전 등급을 가진다.",
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
                    "LLM 없이도 요구사항 분석, 툴 목록, echo가 검증 가능해야 한다.",
                    "Ollama 연결 시 자연어 fallback 응답을 검증한다.",
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
