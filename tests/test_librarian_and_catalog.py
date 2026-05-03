"""LibrarianAgent와 SkillCatalog의 책임 분리 테스트.

A5에서 추가된 계약:
- SkillCatalog.record_usage(name, success): 매니페스트의 usage/failure 카운터 증가
- SkillCatalog.find_stale_entries(): 누락 파일·해시 mismatch 등 무결성 위반 항목 식별
- LibrarianAgent.run(state): catalog가 주입되면 stale_count를 skills_retrieved 이벤트에 노출
- LibrarianAgent.record_usage(name, success): catalog가 없으면 None 반환 no-op
- AdaptiveAgent: 생성 도구 실행 후 generated_tool_usage_recorded 이벤트 발행
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from adaptive_agent.agent import AdaptiveAgent
from adaptive_agent.agents import LibrarianAgent
from adaptive_agent.config import AgentConfig
from adaptive_agent.skills import MANIFEST_FILENAME, SkillCatalog
from adaptive_agent.state import AgentState


class _SilentLLM:
    def complete(self, _prompt: str) -> str:
        return '{"action":"respond","response":"ok"}'


def _seed_manifest_tool(workspace: Path, *, name: str, code: str) -> dict[str, object]:
    """Run the create→validate→approve flow once to seed a manifest entry."""

    agent = AdaptiveAgent(
        config=AgentConfig(
            workspace_dir=workspace,
            tool_library_dir=workspace / ".adaptive_agent" / "tools",
        ),
        llm_client=_SilentLLM(),
    )
    create = agent.run_tool(
        "tool_create",
        {"name": name, "description": "테스트용", "code": code},
    )
    assert create.success, create.error
    validate = agent.run_tool("tool_validate", {"name": name})
    assert validate.success, validate.error
    approve = agent.run_tool("tool_approve", {"name": name})
    assert approve.success, approve.error
    return approve.output


class SkillCatalogRecordUsageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        _seed_manifest_tool(
            self.workspace,
            name="counter_tool",
            code="def run(arguments):\n    return {'ok': True}\n",
        )
        self.catalog = SkillCatalog(self.workspace / ".adaptive_agent" / "tools")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_record_usage_increments_usage_count(self) -> None:
        first = self.catalog.record_usage("counter_tool", success=True)
        second = self.catalog.record_usage("counter_tool", success=True)

        self.assertIsNotNone(first)
        self.assertEqual(first["usage_count"], 1)
        self.assertEqual(first["failure_count"], 0)
        self.assertEqual(second["usage_count"], 2)
        self.assertEqual(second["failure_count"], 0)

    def test_record_usage_failure_bumps_both_counters(self) -> None:
        self.catalog.record_usage("counter_tool", success=True)
        updated = self.catalog.record_usage("counter_tool", success=False)

        self.assertEqual(updated["usage_count"], 2)
        self.assertEqual(updated["failure_count"], 1)

    def test_record_usage_for_unknown_tool_returns_none(self) -> None:
        self.assertIsNone(self.catalog.record_usage("nonexistent", success=True))

    def test_record_usage_persists_to_manifest_on_disk(self) -> None:
        self.catalog.record_usage("counter_tool", success=False)

        manifest_path = self.workspace / ".adaptive_agent" / "tools" / MANIFEST_FILENAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = next(t for t in manifest["tools"] if t["name"] == "counter_tool")
        self.assertEqual(entry["usage_count"], 1)
        self.assertEqual(entry["failure_count"], 1)

    def test_upsert_preserves_stats_on_reapproval(self) -> None:
        """Re-upserting a tool preserves accumulated usage/failure counts."""

        self.catalog.record_usage("counter_tool", success=True)
        self.catalog.record_usage("counter_tool", success=False)

        # Re-upsert with new description (simulating re-approval)
        entry = self.catalog._find_existing("counter_tool")
        updated = self.catalog.upsert({**entry, "description": "updated description"})

        self.assertEqual(updated["usage_count"], 2)
        self.assertEqual(updated["failure_count"], 1)
        self.assertEqual(updated["description"], "updated description")

    def test_delete_removes_existing_entry(self) -> None:
        self.assertTrue(self.catalog.delete("counter_tool"))
        self.assertEqual(self.catalog.list(), [])

    def test_delete_returns_false_for_missing_name(self) -> None:
        self.assertFalse(self.catalog.delete("nonexistent"))

    def test_delete_persists_removal_to_disk(self) -> None:
        self.catalog.delete("counter_tool")

        manifest_path = self.workspace / ".adaptive_agent" / "tools" / MANIFEST_FILENAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        names = [t["name"] for t in manifest["tools"]]
        self.assertNotIn("counter_tool", names)


class SkillCatalogFindStaleEntriesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.tool_library = self.workspace / ".adaptive_agent" / "tools"
        self.catalog = SkillCatalog(self.tool_library)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_no_stale_entries_when_files_intact(self) -> None:
        _seed_manifest_tool(
            self.workspace,
            name="intact_tool",
            code="def run(arguments):\n    return {'ok': True}\n",
        )
        self.assertEqual(self.catalog.find_stale_entries(), [])

    def test_missing_file_is_detected(self) -> None:
        _seed_manifest_tool(
            self.workspace,
            name="will_disappear",
            code="def run(arguments):\n    return {'ok': True}\n",
        )
        (self.tool_library / "will_disappear.py").unlink()

        stale = self.catalog.find_stale_entries()

        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["name"], "will_disappear")
        self.assertEqual(stale[0]["reason"], "missing_file")

    def test_hash_mismatch_is_detected(self) -> None:
        _seed_manifest_tool(
            self.workspace,
            name="will_change",
            code="def run(arguments):\n    return {'ok': True}\n",
        )
        (self.tool_library / "will_change.py").write_text(
            "def run(arguments):\n    return {'mutated': True}\n",
            encoding="utf-8",
        )

        stale = self.catalog.find_stale_entries()

        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["reason"], "hash_mismatch")

    def test_missing_hash_is_detected(self) -> None:
        _seed_manifest_tool(
            self.workspace,
            name="legacy_tool",
            code="def run(arguments):\n    return {'ok': True}\n",
        )
        manifest_path = self.tool_library / MANIFEST_FILENAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["tools"][0].pop("file_hash", None)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        stale = self.catalog.find_stale_entries()

        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["reason"], "missing_hash")


class LibrarianAgentTest(unittest.TestCase):
    def test_run_without_catalog_omits_stale_count(self) -> None:
        librarian = LibrarianAgent(retriever=lambda _s: [{"name": "x"}])
        state = AgentState()

        librarian.run(state)

        skills_events = [e for e in state.events if e.name == "skills_retrieved"]
        self.assertEqual(len(skills_events), 1)
        self.assertNotIn("stale_count", skills_events[0].details)
        self.assertEqual(skills_events[0].details["count"], 1)

    def test_run_with_catalog_surfaces_stale_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_manifest_tool(
                workspace,
                name="audited_tool",
                code="def run(arguments):\n    return {'ok': True}\n",
            )
            tool_library = workspace / ".adaptive_agent" / "tools"
            (tool_library / "audited_tool.py").unlink()  # 의도적 손상

            catalog = SkillCatalog(tool_library)
            librarian = LibrarianAgent(retriever=lambda _s: [], catalog=catalog)
            state = AgentState()

            librarian.run(state)

            skills_events = [e for e in state.events if e.name == "skills_retrieved"]
            self.assertEqual(skills_events[0].details["stale_count"], 1)
            audit_events = [e for e in state.events if e.name == "catalog_audit_stale_entries"]
            self.assertEqual(len(audit_events), 1)
            self.assertEqual(audit_events[0].details["stale"][0]["reason"], "missing_file")

    def test_record_usage_without_catalog_is_noop(self) -> None:
        librarian = LibrarianAgent()
        self.assertIsNone(librarian.record_usage("anything", success=True))

    def test_record_usage_with_catalog_delegates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_manifest_tool(
                workspace,
                name="delegated_tool",
                code="def run(arguments):\n    return {'ok': True}\n",
            )
            catalog = SkillCatalog(workspace / ".adaptive_agent" / "tools")
            librarian = LibrarianAgent(catalog=catalog)

            updated = librarian.record_usage("delegated_tool", success=False)

            self.assertIsNotNone(updated)
            self.assertEqual(updated["usage_count"], 1)
            self.assertEqual(updated["failure_count"], 1)


class GeneratedToolUsageReportingTest(unittest.TestCase):
    def test_generated_tool_run_emits_usage_recorded_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _seed_manifest_tool(
                workspace,
                name="reported_tool",
                code="def run(arguments):\n    return {'value': arguments.get('x')}\n",
            )

            llm_response = (
                '{"action":"tool","tool_name":"reported_tool","arguments":{"x":42}}'
            )

            class _ScriptedLLM:
                def __init__(self) -> None:
                    self.responses = [llm_response]

                def complete(self, _prompt: str) -> str:
                    return self.responses.pop(0) if self.responses else '{"action":"respond","response":"done"}'

            agent = AdaptiveAgent(
                config=AgentConfig(
                    workspace_dir=workspace,
                    tool_library_dir=workspace / ".adaptive_agent" / "tools",
                    max_self_corrections=0,
                ),
                llm_client=_ScriptedLLM(),
            )

            result = agent.run("생성 툴 호출")

            usage_events = [e for e in result.events if e.name == "generated_tool_usage_recorded"]
            self.assertEqual(len(usage_events), 1)
            self.assertEqual(usage_events[0].details["tool_name"], "reported_tool")
            self.assertTrue(usage_events[0].details["success"])
            self.assertEqual(usage_events[0].details["usage_count"], 1)

    def test_builtin_tool_run_does_not_emit_usage_recorded_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            agent = AdaptiveAgent(
                config=AgentConfig(
                    workspace_dir=workspace,
                    tool_library_dir=workspace / ".adaptive_agent" / "tools",
                ),
                llm_client=_SilentLLM(),
            )

            class _EchoLLM:
                def complete(self, _prompt: str) -> str:
                    return '{"action":"tool","tool_name":"echo","arguments":{"task":"hi"}}'

            agent.llm_client = _EchoLLM()
            result = agent.run("echo")

            usage_events = [e for e in result.events if e.name == "generated_tool_usage_recorded"]
            self.assertEqual(usage_events, [], "builtin 도구는 manifest 통계에 잡히지 않아야 합니다")


if __name__ == "__main__":
    unittest.main()
