"""OpenAI client error handling tests."""

from __future__ import annotations

import unittest

from adaptive_agent.llms.openai_client import format_openai_api_error


class OpenAIClientTest(unittest.TestCase):
    def test_model_not_found_error_has_actionable_message(self) -> None:
        message = format_openai_api_error(
            status_code=400,
            model="gpt-5.2-nano",
            message="The requested model 'gpt-5.2-nano' does not exist.",
        )

        self.assertIsNotNone(message)
        self.assertIn("gpt-5.2-nano", message)
        self.assertIn("gpt-5.4-nano", message)
        self.assertIn("gpt-5.4-mini", message)


if __name__ == "__main__":
    unittest.main()
