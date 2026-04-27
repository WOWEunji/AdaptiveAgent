"""AdaptiveAgent 기본 동작 테스트."""

from __future__ import annotations

from adaptive_agent.agent import AdaptiveAgent
from adaptive_agent.config import AgentConfig


class StubLLM:
    """테스트용 LLM 클라이언트."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "LLM 응답"


def test_empty_task_returns_guidance() -> None:
    agent = AdaptiveAgent(config=AgentConfig(), llm_client=StubLLM())

    result = agent.run("   ")

    assert result.output == "작업 내용을 입력해 주세요."
    assert result.tool_name is None


def test_builtin_echo_tool_matches_keyword() -> None:
    llm = StubLLM()
    agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

    result = agent.run("echo 안녕하세요")

    assert result.output == "echo 안녕하세요"
    assert result.tool_name == "echo"
    assert llm.prompts == []


def test_llm_handles_unmatched_task() -> None:
    llm = StubLLM()
    agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

    result = agent.run("새로운 툴을 설계해줘")

    assert result.output == "LLM 응답"
    assert result.tool_name is None
    assert "새로운 툴을 설계해줘" in llm.prompts[0]
