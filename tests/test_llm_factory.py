"""LLM factory routing tests."""

from __future__ import annotations

import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from adaptive_agent.config import AgentConfig
from adaptive_agent.llms.factory import create_llm_client
from adaptive_agent.llms.ollama import OllamaClient


class LLMFactoryTest(unittest.TestCase):
    def test_ollama_client_receives_runtime_config(self) -> None:
        config = AgentConfig(
            ollama_model="qwen-test",
            ollama_host="http://localhost:11434",
            ollama_timeout_seconds=12.5,
            ollama_num_predict=64,
            ollama_think=True,
        )

        client = create_llm_client(config, provider="ollama")

        self.assertEqual(client.model, "qwen-test")
        self.assertEqual(client.host, "http://localhost:11434")
        self.assertEqual(client.timeout_seconds, 12.5)
        self.assertEqual(client.num_predict, 64)
        self.assertTrue(client.think)

    def test_openai_provider_uses_configured_model(self) -> None:
        config = AgentConfig(openai_model="gpt-5-nano")

        with patch("adaptive_agent.llms.openai_client.OpenAIClient") as client_class:
            create_llm_client(config, provider="openai")

        client_class.assert_called_once_with(model="gpt-5-nano")

    def test_gemini_provider_uses_configured_model(self) -> None:
        config = AgentConfig(gemini_model="gemini-2.5-flash-lite")

        with patch("adaptive_agent.llms.gemini_client.GeminiClient") as client_class:
            create_llm_client(config, provider="gemini")

        client_class.assert_called_once_with(model="gemini-2.5-flash-lite")

    def test_ollama_client_uses_configured_host(self) -> None:
        ollama_client = Mock()
        ollama_client.chat.return_value = {"message": {"content": "ok"}}
        ollama_module = SimpleNamespace(Client=Mock(return_value=ollama_client))

        with patch.dict(sys.modules, {"ollama": ollama_module}):
            result = OllamaClient(
                model="qwen-test",
                host="http://ollama.test:11434",
                timeout_seconds=12.5,
                num_predict=64,
                think=False,
            ).complete("hello")

        self.assertEqual(result, "ok")
        ollama_module.Client.assert_called_once_with(host="http://ollama.test:11434", timeout=12.5)
        ollama_client.chat.assert_called_once_with(
            model="qwen-test",
            messages=[{"role": "user", "content": "hello"}],
            options={"temperature": 0, "num_predict": 64},
            think=False,
        )


if __name__ == "__main__":
    unittest.main()
