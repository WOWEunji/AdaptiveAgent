"""Built-in tool behavior tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adaptive_agent.tools.registry import create_default_registry


class BuiltinToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = create_default_registry(self.workspace)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_tool(self, name: str, arguments: dict[str, object]):
        tool = self.registry.get(name)
        self.assertIsNotNone(tool)
        return tool.handler(arguments)  # type: ignore[union-attr]

    def test_code_execute_returns_process_output_and_expectation_verdict(self) -> None:
        result = self.run_tool(
            "code_execute",
            {
                "code": "print('answer: 42')",
                "lang": "python",
                "expected_output": "42",
            },
        )

        self.assertTrue(result.success)
        self.assertEqual(result.output["execution"]["exit_code"], 0)
        self.assertIn("answer: 42", result.output["execution"]["stdout"])
        self.assertTrue(result.output["verdict"]["matches_expectation"])
        self.assertTrue(result.output["execution"]["sandbox"]["process_isolated"])

    def test_code_execute_fails_when_successful_process_misses_expected_output(self) -> None:
        result = self.run_tool(
            "code_execute",
            {
                "code": "print('actual')",
                "expected_output": "wanted",
            },
        )

        self.assertFalse(result.success)
        self.assertEqual(result.output["execution"]["exit_code"], 0)
        self.assertFalse(result.output["verdict"]["checks"]["stdout_contains_expected_output"])
        self.assertEqual(result.error, "프로세스는 실행되었지만 기대 결과 검증에 실패했습니다.")

    def test_shell_run_executes_in_temporary_directory(self) -> None:
        result = self.run_tool(
            "shell_run",
            {
                "code": "pwd; touch created.txt; echo ok",
                "expected_stdout_contains": "ok",
            },
        )

        self.assertTrue(result.success)
        self.assertIn("ok", result.output["execution"]["stdout"])
        self.assertFalse((self.workspace / "created.txt").exists())
        self.assertEqual(result.output["execution"]["sandbox"]["working_directory"], "temporary")

    def test_shell_run_timeout_is_reported_as_failure(self) -> None:
        result = self.run_tool("shell_run", {"code": "sleep 1", "timeout_seconds": "0.1"})

        self.assertFalse(result.success)
        self.assertTrue(result.output["execution"]["timed_out"])
        self.assertEqual(result.output["execution"]["exit_code"], 124)

    def test_file_read_and_write_stay_inside_workspace(self) -> None:
        write_result = self.run_tool("file_write", {"path": "notes/hello.txt", "content": "안녕"})
        read_result = self.run_tool("file_read", {"path": "notes/hello.txt"})

        self.assertTrue(write_result.success)
        self.assertEqual(write_result.output["path"], "notes/hello.txt")
        self.assertTrue(read_result.success)
        self.assertEqual(read_result.output["content"], "안녕")

        outside_result = self.run_tool("file_read", {"path": "../outside.txt"})
        self.assertFalse(outside_result.success)
        self.assertIn("Workspace 밖", outside_result.error)

    def test_file_write_blocks_sensitive_paths(self) -> None:
        result = self.run_tool("file_write", {"path": ".env", "content": "SECRET=1"})

        self.assertFalse(result.success)
        self.assertFalse((self.workspace / ".env").exists())
        self.assertIn("민감한 경로", result.error)

    def test_human_in_the_loop_tools_return_pending_state(self) -> None:
        ask_result = self.run_tool(
            "ask_human",
            {"questions": ["진행할까요?"], "options": ["yes", "no"]},
        )
        propose_result = self.run_tool(
            "propose_actions",
            {"plan": {"steps": ["write file"]}, "risk_level": "high"},
        )

        self.assertTrue(ask_result.success)
        self.assertEqual(ask_result.output["status"], "pending_human_input")
        self.assertEqual(ask_result.output["options"], ["yes", "no"])
        self.assertTrue(propose_result.success)
        self.assertEqual(propose_result.output["status"], "approval_required")
        self.assertFalse(propose_result.output["approved"])

    def test_tool_create_and_search_generated_tool_metadata(self) -> None:
        create_result = self.run_tool(
            "tool_create",
            {
                "name": "hello_tool",
                "description": "Greets a user",
                "code": "def run(arguments):\n    return {'hello': arguments.get('name')}\n",
            },
        )
        search_result = self.run_tool("tool_search", {"query": "greet"})

        self.assertTrue(create_result.success)
        self.assertEqual(create_result.output["status"], "created_unloaded")
        self.assertTrue((self.workspace / ".adaptive_agent" / "tools" / "hello_tool.py").exists())
        self.assertTrue(search_result.success)
        self.assertIn("hello_tool", {match["name"] for match in search_result.output["matches"]})

    def test_tool_search_finds_builtin_tools(self) -> None:
        result = self.run_tool("tool_search", {"query": "file"})

        self.assertTrue(result.success)
        names = {match["name"] for match in result.output["matches"]}
        self.assertIn("file_read", names)
        self.assertIn("file_write", names)

    def test_suggest_builtin_tools_includes_additional_candidates(self) -> None:
        result = self.run_tool("suggest_builtin_tools", {})

        self.assertTrue(result.success)
        names = {candidate["name"] for candidate in result.output}
        self.assertIn("file_patch", names)
        self.assertIn("tool_validate", names)


if __name__ == "__main__":
    unittest.main()
