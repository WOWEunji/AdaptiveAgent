"""Streaming LLM 응답 테스트.

신규 LLMClient.stream() 프로토콜과 agent.stream_response() + CLI --stream
플래그 검증. plan/JSON 흐름은 건드리지 않음.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from adaptive_agent.agent import AdaptiveAgent
from adaptive_agent.cli import main
from adaptive_agent.config import AgentConfig


class _ChunkStreamLLM:
    """단일 chunks 리스트를 yield하는 stub."""

    def __init__(self, chunks: list[str], complete_response: str = "fallback complete"):
        self.chunks = chunks
        self.complete_response = complete_response
        self.complete_calls = 0
        self.stream_calls = 0

    def complete(self, _prompt: str) -> str:
        self.complete_calls += 1
        return self.complete_response

    def generate(self, prompt: str) -> str:
        return self.complete(prompt)

    def stream(self, _prompt: str):
        self.stream_calls += 1
        yield from self.chunks


class _NoStreamLLM:
    """stream 메서드 없는 stub — fallback 검증용."""

    def complete(self, _prompt: str) -> str:
        return "no-stream fallback"

    def generate(self, prompt: str) -> str:
        return self.complete(prompt)


class StreamResponseTest(unittest.TestCase):
    def test_yields_provider_chunks_in_order(self) -> None:
        llm = _ChunkStreamLLM(["Hel", "lo ", "world"])
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        chunks = list(agent.stream_response("greet"))

        self.assertEqual(chunks, ["Hel", "lo ", "world"])
        self.assertEqual(llm.stream_calls, 1)
        self.assertEqual(llm.complete_calls, 0, "stream이 있으면 complete는 호출되지 않아야 함")

    def test_falls_back_to_complete_when_provider_lacks_stream(self) -> None:
        llm = _NoStreamLLM()
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        chunks = list(agent.stream_response("anything"))

        self.assertEqual(chunks, ["no-stream fallback"])

    def test_empty_chunks_are_filtered(self) -> None:
        llm = _ChunkStreamLLM(["", "data", "", "more", ""])
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)

        chunks = list(agent.stream_response("x"))

        self.assertEqual(chunks, ["data", "more"])


class CLIStreamFlagTest(unittest.TestCase):
    def test_stream_flag_prints_chunks_to_stdout(self) -> None:
        buffer = io.StringIO()
        chunks = ["First. ", "Second. ", "Third."]

        with patch("adaptive_agent.cli.AdaptiveAgent") as agent_class, redirect_stdout(buffer):
            agent_class.return_value.stream_response.return_value = iter(chunks)

            exit_code = main(["--stream", "stream test"])

        self.assertEqual(exit_code, 0)
        output = buffer.getvalue()
        self.assertIn("First. Second. Third.", output)

    def test_stream_flag_handles_provider_exception(self) -> None:
        buffer = io.StringIO()

        def _raising_iter(*_args, **_kwargs):
            yield "partial "
            raise RuntimeError("provider boom")

        with patch("adaptive_agent.cli.AdaptiveAgent") as agent_class, redirect_stdout(buffer):
            agent_class.return_value.stream_response.return_value = _raising_iter()

            exit_code = main(["--stream", "boom"])

        self.assertEqual(exit_code, 1)
        output = buffer.getvalue()
        self.assertIn("partial", output)
        self.assertIn("stream 실패", output)


if __name__ == "__main__":
    unittest.main()
