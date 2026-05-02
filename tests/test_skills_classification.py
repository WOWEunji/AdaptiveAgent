"""Skills 분류 정책(planning/functional/atomic) 테스트.

R5 (requirements_breakdown.md) — 모든 도구는 세 분류 중 정확히 하나에
속해야 한다. ``Tool.skill_class``는 도구 등록 시점에 강제되고, generated
도구는 manifest의 ``skill_class`` 필드(없으면 'functional' 폴백)를 따른다.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adaptive_agent.skills import SkillCatalog
from adaptive_agent.tools.models import (
    SKILL_CLASS_ATOMIC,
    SKILL_CLASS_FUNCTIONAL,
    SKILL_CLASS_PLANNING,
    SKILL_CLASSES,
    Tool,
    ToolExecutionResult,
)
from adaptive_agent.tools.registry import create_default_registry


def _noop(_args):
    return ToolExecutionResult(success=True, output=None)


class SkillClassEnumTest(unittest.TestCase):
    def test_three_classes_are_defined(self) -> None:
        self.assertEqual(SKILL_CLASSES, frozenset({"planning", "functional", "atomic"}))
        self.assertEqual(SKILL_CLASS_PLANNING, "planning")
        self.assertEqual(SKILL_CLASS_FUNCTIONAL, "functional")
        self.assertEqual(SKILL_CLASS_ATOMIC, "atomic")


class ToolValidationTest(unittest.TestCase):
    def test_valid_skill_class_accepted(self) -> None:
        for cls in SKILL_CLASSES:
            with self.subTest(skill_class=cls):
                tool = Tool(name=f"t_{cls}", description="d", handler=_noop, skill_class=cls)
                self.assertEqual(tool.skill_class, cls)

    def test_invalid_skill_class_rejected(self) -> None:
        for bad in ("invalid", "PLANNING", "operational", ""):
            with self.subTest(skill_class=bad):
                with self.assertRaises(ValueError) as ctx:
                    Tool(name="bad", description="d", handler=_noop, skill_class=bad)
                self.assertIn("skill_class", str(ctx.exception))

    def test_default_skill_class_is_functional(self) -> None:
        tool = Tool(name="defaulted", description="d", handler=_noop)
        self.assertEqual(tool.skill_class, "functional")


class DefaultRegistrySkillClassesTest(unittest.TestCase):
    """등록된 모든 builtin이 planning/functional/atomic 중 하나에 속함."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.registry = create_default_registry(self.workspace)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_every_builtin_has_valid_skill_class(self) -> None:
        for tool in self.registry.list():
            with self.subTest(tool=tool.name):
                self.assertIn(tool.skill_class, SKILL_CLASSES)

    def test_known_planning_tools(self) -> None:
        # 명시적 회귀: 정확히 어떤 도구가 planning에 들어가는지 잠금
        planning = {tool.name for tool in self.registry.list() if tool.skill_class == "planning"}
        self.assertIn("analyze_requirements", planning)
        self.assertIn("list_tools", planning)
        self.assertIn("tool_search", planning)
        self.assertIn("suggest_builtin_tools", planning)

    def test_known_atomic_tools(self) -> None:
        atomic = {tool.name for tool in self.registry.list() if tool.skill_class == "atomic"}
        self.assertIn("echo", atomic)
        self.assertIn("ask_human", atomic)
        self.assertIn("propose_actions", atomic)

    def test_known_functional_tools(self) -> None:
        functional = {tool.name for tool in self.registry.list() if tool.skill_class == "functional"}
        for expected in {
            "code_execute",
            "shell_run",
            "file_read",
            "file_write",
            "file_list",
            "file_patch",
            "test_run",
            "tool_create",
            "tool_validate",
            "tool_approve",
            "memory_read",
            "memory_write",
        }:
            self.assertIn(expected, functional, f"{expected}는 functional이어야 함")


class GeneratedToolSkillClassTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.tool_library = self.workspace / ".adaptive_agent" / "tools"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_manifest_normalize_defaults_skill_class_to_functional(self) -> None:
        catalog = SkillCatalog(self.tool_library)
        normalized = catalog._normalize({"name": "anon_tool", "description": "x"}, use_existing=False)
        self.assertEqual(normalized["skill_class"], "functional")

    def test_manifest_normalize_preserves_explicit_skill_class(self) -> None:
        catalog = SkillCatalog(self.tool_library)
        for cls in ("planning", "functional", "atomic"):
            with self.subTest(skill_class=cls):
                normalized = catalog._normalize(
                    {"name": f"t_{cls}", "skill_class": cls}, use_existing=False
                )
                self.assertEqual(normalized["skill_class"], cls)

    def test_manifest_normalize_falls_back_for_invalid_class(self) -> None:
        catalog = SkillCatalog(self.tool_library)
        normalized = catalog._normalize({"name": "weird", "skill_class": "invalid"}, use_existing=False)
        self.assertEqual(normalized["skill_class"], "functional")


if __name__ == "__main__":
    unittest.main()
