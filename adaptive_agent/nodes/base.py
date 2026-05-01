"""Shared contracts for Plan, Coder, Critic, and Skill agent nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from adaptive_agent.state import AgentState, NodeName


@dataclass(frozen=True)
class NodeResult:
    """노드 실행 후 라우터가 다음 전이를 판단할 수 있는 표준 결과입니다."""

    next_node: NodeName
    status: str = "ok"
    details: dict[str, object] = field(default_factory=dict)


class AgentNode(Protocol):
    """모든 역할별 에이전트 노드가 따르는 최소 인터페이스입니다."""

    name: NodeName
    prompt_set: str
    prompt_template: str

    def run(self, state: AgentState) -> NodeResult:
        """공유 AgentState를 읽고 갱신한 뒤 다음 노드를 반환합니다."""


@dataclass
class BaseAgentNode:
    """역할별 prompt 위치 규칙을 명시하는 기본 노드입니다."""

    name: NodeName
    prompt_template: str
    prompt_set: str = "default"

    @property
    def prompt_path(self) -> str:
        """프롬프트 파일은 `prompts/<prompt_set>/<prompt_template>`에 둡니다."""

        return f"{self.prompt_set}/{self.prompt_template}"
