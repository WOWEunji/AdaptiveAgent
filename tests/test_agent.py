"""AdaptiveAgent 기본 동작 테스트."""

from __future__ import annotations

import unittest

from adaptive_agent.agent import AdaptiveAgent
from adaptive_agent.config import AgentConfig


class StubLLM:
    """테스트용 LLM 클라이언트."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "LLM 응답"


class AdaptiveAgentTest(unittest.TestCase):
    def test_empty_task_returns_guidance(self) -> None:
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=StubLLM())

        result = agent.run("   ")

        self.assertEqual(result.output, "작업 내용을 입력해 주세요.")
        self.assertIsNone(result.tool_name)

    def test_builtin_echo_tool_matches_keyword(self) -> None:
        llm = StubLLM()
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        result = agent.run("echo 안녕하세요")

        self.assertEqual(result.output, "echo 안녕하세요")
        self.assertEqual(result.tool_name, "echo")
        self.assertEqual(llm.prompts, [])

    def test_llm_handles_unmatched_task(self) -> None:
        llm = StubLLM()
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        result = agent.run("새로운 툴을 설계해줘")

        self.assertEqual(result.output, "LLM 응답")
        self.assertIsNone(result.tool_name)
        self.assertIn("새로운 툴을 설계해줘", llm.prompts[0])


if __name__ == "__main__":
    unittest.main()
