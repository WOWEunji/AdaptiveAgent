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

    def test_resume_error_is_user_friendly(self) -> None:
        buffer = io.StringIO()
        session_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        underlying_message = "pending 상태의 세션만 재개할 수 있습니다"
        with patch("adaptive_agent.cli.AdaptiveAgent") as agent_class, redirect_stdout(buffer):
            agent_class.return_value.resume.side_effect = ValueError(underlying_message)

            exit_code = main(["--resume", session_id, "--input", "ok"])

        # 사용자에게 노출되는 라벨 문구는 자유 영역 — 구조적 계약만 검증
        self.assertEqual(exit_code, 1)
        output = buffer.getvalue()
        self.assertIn(session_id, output, "어떤 세션이 실패했는지 식별 가능해야 합니다")
        self.assertIn(underlying_message, output, "원인 메시지가 사용자에게 전달되어야 합니다")

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
