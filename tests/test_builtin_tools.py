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
        self.assertEqual(result.output["execution"]["sandbox"]["backend"], "local_process")

    def test_shell_run_timeout_is_reported_as_failure(self) -> None:
        result = self.run_tool("shell_run", {"code": "sleep 1", "timeout_seconds": "0.1"})

        self.assertFalse(result.success)
        self.assertTrue(result.output["execution"]["timed_out"])
        self.assertEqual(result.output["execution"]["exit_code"], 124)

    def test_code_execute_blocks_real_workspace_absolute_path(self) -> None:
        result = self.run_tool(
            "code_execute",
            {"code": f"open({str(self.workspace / 'leak.txt')!r}, 'w').write('x')"},
        )

        self.assertFalse(result.success)
        self.assertTrue(result.output["verdict"]["policy_blocked"])
        self.assertFalse((self.workspace / "leak.txt").exists())

    def test_shell_run_blocks_destructive_patterns(self) -> None:
        result = self.run_tool("shell_run", {"code": "rm -rf created.txt"})

        self.assertFalse(result.success)
        self.assertTrue(result.output["verdict"]["policy_blocked"])

    def test_shell_run_blocks_unquoted_sensitive_absolute_paths(self) -> None:
        result = self.run_tool("shell_run", {"code": "cat /etc/passwd"})

        self.assertFalse(result.success)
        self.assertTrue(result.output["verdict"]["policy_blocked"])
        self.assertIn("/etc", result.error)

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

    def test_file_list_returns_structured_entries(self) -> None:
        (self.workspace / "src").mkdir()
        (self.workspace / "src" / "app.py").write_text("print('ok')", encoding="utf-8")
        (self.workspace / "src" / ".env").write_text("SECRET=1", encoding="utf-8")

        result = self.run_tool("file_list", {"path": ".", "pattern": "*.py", "recursive": "true"})

        self.assertTrue(result.success)
        paths = {entry["path"] for entry in result.output["entries"]}
        self.assertIn("src/app.py", paths)
        self.assertNotIn("src/.env", paths)

    def test_file_patch_supports_dry_run_and_apply(self) -> None:
        target = self.workspace / "notes.txt"
        target.write_text("hello old\n", encoding="utf-8")

        dry_run = self.run_tool(
            "file_patch",
            {"path": "notes.txt", "old_text": "old", "new_text": "new", "dry_run": "true"},
        )
        apply_result = self.run_tool(
            "file_patch",
            {"path": "notes.txt", "old_text": "old", "new_text": "new"},
        )

        self.assertTrue(dry_run.success)
        self.assertIn("-hello old", dry_run.output["diff"])
        self.assertEqual(target.read_text(encoding="utf-8"), "hello new\n")
        self.assertTrue(apply_result.success)

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

    def test_test_run_uses_workspace_copy(self) -> None:
        result = self.run_tool(
            "test_run",
            {
                "command": "python3 -c \"from pathlib import Path; Path('created.txt').write_text('x'); print('ok')\"",
                "expected_stdout_contains": "ok",
            },
        )

        self.assertTrue(result.success)
        self.assertFalse((self.workspace / "created.txt").exists())
        self.assertEqual(result.output["execution"]["sandbox"]["filesystem_isolation"], "workspace_copy")

    def test_test_run_blocks_real_workspace_absolute_path(self) -> None:
        result = self.run_tool(
            "test_run",
            {
                "command": (
                    "python3 -c \"from pathlib import Path; "
                    f"Path({str(self.workspace / 'created.txt')!r}).write_text('x')\""
                )
            },
        )

        self.assertFalse(result.success)
        self.assertTrue(result.output["verdict"]["policy_blocked"])
        self.assertFalse((self.workspace / "created.txt").exists())

    def test_test_run_skips_workspace_symlinks(self) -> None:
        outside = Path(self.temp_dir.name).parent / "outside-adaptive-agent-test.txt"
        outside.write_text("secret", encoding="utf-8")
        try:
            (self.workspace / "outside_link.txt").symlink_to(outside)
            result = self.run_tool(
                "test_run",
                {
                    "command": (
                        "python3 -c \"from pathlib import Path; "
                        "print(Path('outside_link.txt').exists())\""
                    ),
                    "expected_stdout_contains": "False",
                },
            )
        finally:
            outside.unlink(missing_ok=True)

        self.assertTrue(result.success)

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

    def test_tool_validate_runs_generated_tool_in_sandbox(self) -> None:
        self.run_tool(
            "tool_create",
            {
                "name": "hello_tool",
                "description": "Greets a user",
                "code": "def run(arguments):\n    return {'hello': arguments.get('name')}\n",
            },
        )

        result = self.run_tool(
            "tool_validate",
            {
                "name": "hello_tool",
                "sample_arguments": {"name": "Ada"},
                "expected_output": '"hello": "Ada"',
            },
        )

        self.assertTrue(result.success)
        self.assertEqual(result.output["tool"]["status"], "validated")
        self.assertIn('"hello": "Ada"', result.output["execution"]["stdout"])

    def test_tool_validate_policy_blocks_generated_tool_side_effects(self) -> None:
        self.run_tool(
            "tool_create",
            {
                "name": "side_effect_tool",
                "description": "Attempts a real workspace write",
                "code": (
                    f"from pathlib import Path\n"
                    f"Path({str(self.workspace / 'side_effect.txt')!r}).write_text('x')\n"
                    "def run(arguments):\n"
                    "    return {'ok': True}\n"
                ),
            },
        )

        result = self.run_tool("tool_validate", {"name": "side_effect_tool"})

        self.assertFalse(result.success)
        self.assertTrue(result.output["verdict"]["policy_blocked"])
        self.assertFalse((self.workspace / "side_effect.txt").exists())

    def test_tool_search_finds_builtin_tools(self) -> None:
        result = self.run_tool("tool_search", {"query": "file"})

        self.assertTrue(result.success)
        names = {match["name"] for match in result.output["matches"]}
        self.assertIn("file_read", names)
        self.assertIn("file_write", names)

    def test_memory_read_and_write_store_structured_values(self) -> None:
        write_result = self.run_tool("memory_write", {"key": "preference", "value": "한국어"})
        read_result = self.run_tool("memory_read", {"key": "preference"})

        self.assertTrue(write_result.success)
        self.assertTrue(read_result.success)
        self.assertEqual(read_result.output["value"], "한국어")

    def test_suggest_builtin_tools_includes_remaining_candidates(self) -> None:
        result = self.run_tool("suggest_builtin_tools", {})

        self.assertTrue(result.success)
        names = {candidate["name"] for candidate in result.output}
        self.assertIn("artifact_store", names)
        self.assertIn("web_fetch", names)


if __name__ == "__main__":
    unittest.main()
