"""CLI smoke tests."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from adaptive_agent.cli import main


class CliTest(unittest.TestCase):
    def test_json_output_for_explicit_tool(self) -> None:
        buffer = io.StringIO()
        with patch("sys.argv", ["adaptive-agent"]), redirect_stdout(buffer):
            exit_code = main(["--json", "--tool", "echo", "--arg", "task=ping"])

        self.assertEqual(exit_code, 0)
        self.assertIn('"tool_name": "echo"', buffer.getvalue())
        self.assertIn('"action": "tool"', buffer.getvalue())
        self.assertIn('"success": true', buffer.getvalue())

    def test_list_tools_output(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = main(["--list-tools"])

        self.assertEqual(exit_code, 0)
        self.assertIn("analyze_requirements", buffer.getvalue())

    def test_list_tools_json_output(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = main(["--list-tools", "--json"])

        self.assertEqual(exit_code, 0)
        self.assertIn('"name": "list_files"', buffer.getvalue())

    def test_natural_language_task_keeps_original_spacing(self) -> None:
        buffer = io.StringIO()
        with patch("adaptive_agent.cli.AdaptiveAgent") as agent_class, redirect_stdout(buffer):
            agent = agent_class.return_value
            agent.run.return_value.task = "  원문  유지  "
            agent.run.return_value.output = "ok"
            agent.run.return_value.tool_name = None
            agent.run.return_value.action = "llm"

            exit_code = main(["--json", "  원문  유지  "])

        self.assertEqual(exit_code, 0)
        agent.run.assert_called_once_with("  원문  유지  ")
        self.assertIn('"task": "  원문  유지  "', buffer.getvalue())

    def test_natural_language_requires_single_argument(self) -> None:
        error_buffer = io.StringIO()
        with redirect_stderr(error_buffer), self.assertRaises(SystemExit):
            main(["분리된", "입력"])

    def test_multiple_task_arguments_are_rejected_to_preserve_input(self) -> None:
        error_buffer = io.StringIO()
        with redirect_stderr(error_buffer), self.assertRaises(SystemExit):
            main(["원문", "분리"])


if __name__ == "__main__":
    unittest.main()
