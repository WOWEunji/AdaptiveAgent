"""OpenAI 라우팅·키 검증 단위 테스트."""

from __future__ import annotations

import unittest

from adaptive_agent.llms.openai_client import (
    should_use_openai_responses_api,
    validate_openai_api_key,
)


class OpenAIRoutingTest(unittest.TestCase):
    def test_should_use_openai_responses_api_gpt5_family(self) -> None:
        self.assertTrue(should_use_openai_responses_api("gpt-5-nano"))
        self.assertTrue(should_use_openai_responses_api("gpt-5-nano-2025-08-07"))
        self.assertFalse(should_use_openai_responses_api("gpt-4o-mini"))
        self.assertFalse(should_use_openai_responses_api("gpt-4.1-nano"))

    def test_validate_openai_api_key_rejects_placeholder(self) -> None:
        placeholders = [
            "your_openai_key_here",
            "your-api-key",
            "changeme",
            "placeholder",
            "paste_here",
            "sk-test",
        ]
        for placeholder in placeholders:
            with self.subTest(placeholder=placeholder):
                with self.assertRaises(ValueError):
                    validate_openai_api_key(placeholder)

    def test_validate_openai_api_key_accepts_sk_prefix(self) -> None:
        key = "sk-" + "a" * 45
        self.assertEqual(validate_openai_api_key(key), key)


if __name__ == "__main__":
    unittest.main()
