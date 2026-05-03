"""LLM factory routing tests."""

from __future__ import annotations

import json
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch

from adaptive_agent.config import AgentConfig
from adaptive_agent.llms.factory import create_llm_client
from adaptive_agent.llms.ollama import OllamaClient


class LLMFactoryTest(unittest.TestCase):
    def test_ollama_client_receives_runtime_config(self) -> None:
        config = AgentConfig(
            ollama_model="qwen-test",
            ollama_host="http://localhost",
            ollama_port=12345,
            ollama_timeout_seconds=12.5,
            ollama_num_predict=64,
            ollama_think=True,
        )

        client = create_llm_client(config, provider="ollama")

        self.assertEqual(client.model, "qwen-test")
        self.assertEqual(client._base, "http://localhost:12345")
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

    def test_ollama_client_calls_http_api(self) -> None:
        response_body = json.dumps({"message": {"content": "ok"}}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            result = OllamaClient(
                model="qwen-test",
                host="127.0.0.1",
                port=11434,
                timeout_seconds=12.5,
                num_predict=64,
                think=False,
            ).complete("hello")

        self.assertEqual(result, "ok")
        call_args = mock_open.call_args
        req = call_args[0][0]
        self.assertIn("/api/chat", req.full_url)
        sent = json.loads(req.data)
        self.assertEqual(sent["model"], "qwen-test")
        self.assertEqual(sent["messages"][0]["content"], "hello")
        self.assertEqual(sent["options"]["temperature"], 0)
        self.assertIn("think", sent)

    def test_ollama_client_default_base_url(self) -> None:
        client = OllamaClient(model="qwen3.5:2b")
        self.assertEqual(client._base, "http://127.0.0.1:11434")

    def test_ollama_client_custom_host_and_port(self) -> None:
        client = OllamaClient(model="m", host="0.0.0.0", port=9999)
        self.assertEqual(client._base, "http://0.0.0.0:9999")


if __name__ == "__main__":
    unittest.main()
