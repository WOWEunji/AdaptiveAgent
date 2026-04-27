"""CLI smoke tests."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from adaptive_agent.cli import main


class CliTest(unittest.TestCase):
    def test_json_output_for_builtin_tool(self) -> None:
        buffer = io.StringIO()
        with patch("sys.argv", ["adaptive-agent"]), redirect_stdout(buffer):
            exit_code = main(["--json", "ping"])

        self.assertEqual(exit_code, 0)
        self.assertIn('"tool_name": "echo"', buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
