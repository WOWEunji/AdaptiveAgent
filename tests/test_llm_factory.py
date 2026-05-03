"""LLM factory routing tests."""

from __future__ import annotations

import json
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch

from adaptive_agent.config import AgentConfig
from adaptive_agent.llms.factory import create_llm_client
from adaptive_agent.llms.ollama import OllamaClient
from adaptive_agent.llms.openrouter_client import (
    OpenRouterClient,
    validate_openrouter_api_key,
)


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


class OpenRouterClientTest(unittest.TestCase):
    def test_factory_returns_openrouter_client(self) -> None:
        config = AgentConfig(
            openrouter_model="openai/gpt-4.1-nano",
            openrouter_api_key="sk-or-v1-valid-key",
        )
        with patch("adaptive_agent.llms.openrouter_client.OpenRouterClient") as client_class:
            create_llm_client(config, provider="openrouter")
        client_class.assert_called_once_with(
            model="openai/gpt-4.1-nano",
            api_key="sk-or-v1-valid-key",
        )

    def test_validate_key_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            validate_openrouter_api_key("")

    def test_validate_key_rejects_none(self) -> None:
        with self.assertRaises(ValueError):
            validate_openrouter_api_key(None)

    def test_validate_key_rejects_placeholder(self) -> None:
        with self.assertRaises(ValueError):
            validate_openrouter_api_key("your_openrouter_key")

    def test_validate_key_accepts_real_key(self) -> None:
        key = validate_openrouter_api_key("sk-or-v1-abc123")
        self.assertEqual(key, "sk-or-v1-abc123")

    def test_complete_calls_openai_sdk_with_openrouter_base_url(self) -> None:
        """complete()가 OpenAI SDK를 OpenRouter base URL로 호출하는지 검증한다."""
        mock_message = MagicMock()
        mock_message.content = "42"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_openai_instance = MagicMock()
        mock_openai_instance.chat.completions.create.return_value = mock_response

        with patch(
            "openai.OpenAI",
            return_value=mock_openai_instance,
        ) as mock_openai_cls:
            client = OpenRouterClient(model="openai/gpt-4.1-nano", api_key="sk-or-v1-test")
            result = client.complete("hello")

        self.assertEqual(result, "42")
        init_kwargs = mock_openai_cls.call_args.kwargs
        self.assertIn("openrouter.ai", init_kwargs.get("base_url", ""))
        call_kwargs = mock_openai_instance.chat.completions.create.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "openai/gpt-4.1-nano")


if __name__ == "__main__":
    unittest.main()
