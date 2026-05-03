"""CLI smoke tests."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr

from adaptive_agent.cli import main


class CliTest(unittest.TestCase):
    def test_unrecognized_args_exit(self) -> None:
        """Unknown flags produce SystemExit(2)."""
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["--unknown-flag"])

    def test_multiple_positional_args_rejected(self) -> None:
        """Multiple bare words that aren't a subcommand raise SystemExit."""
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["분리된", "입력"])

    def test_multiple_task_arguments_are_rejected_to_preserve_input(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["원문", "분리"])

    def test_test_subcommand_requires_llm(self) -> None:
        """adaptive-agent test without --llm returns exit code 2."""
        with redirect_stderr(io.StringIO()):
            result = main(["test"])
        self.assertEqual(result, 2)


if __name__ == "__main__":
    unittest.main()
