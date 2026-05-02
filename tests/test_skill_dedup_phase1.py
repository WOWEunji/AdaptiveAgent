"""SkillCatalog 이름 충돌 자동 병합 Phase 1 테스트.

#16 Phase 1 — 이름 충돌은 자동 merge로 두되, 다음 두 가지를 보장한다:

1. ``usage_count``/``failure_count``/``reflections``가 새 metadata에서
   누락되어도 기존 값이 보존된다. (회귀 방지)
2. 병합이 실제로 일어난 경우 ``upsert_with_diff``가 ``merged=True`` +
   pre-merge 카운터를 반환한다.
3. ``tool_approve`` 핸들러 출력이 ``manifest_merge`` 키를 노출하고,
   AdaptiveAgent가 이를 ``manifest_entry_merged`` 이벤트로 변환한다.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from adaptive_agent.agent import AdaptiveAgent
from adaptive_agent.config import AgentConfig
from adaptive_agent.skills import MANIFEST_FILENAME, SkillCatalog


def _seed_manifest_tool(workspace: Path, *, name: str, code: str) -> dict[str, object]:
    class _SilentLLM:
        def complete(self, _p):
            return '{"action":"respond","response":"ok"}'

    agent = AdaptiveAgent(
        config=AgentConfig(
            workspace_dir=workspace,
            tool_library_dir=workspace / ".adaptive_agent" / "tools",
            session_dir=workspace / ".adaptive_agent" / "sessions",
        ),
        llm_client=_SilentLLM(),
    )
    create = agent.run_tool("tool_create", {"name": name, "description": "p1", "code": code})
    assert create.success, create.error
    validate = agent.run_tool("tool_validate", {"name": name})
    assert validate.success, validate.error
    approve = agent.run_tool("tool_approve", {"name": name})
    assert approve.success, approve.error
    return approve.output


class UpsertWithDiffTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.tool_library = self.workspace / ".adaptive_agent" / "tools"
        self.catalog = SkillCatalog(self.tool_library)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_first_upsert_reports_not_merged(self) -> None:
        result = self.catalog.upsert_with_diff({"name": "fresh_tool", "description": "first"})
        self.assertFalse(result["merged"])
        self.assertEqual(result["previous_usage_count"], 0)
        self.assertEqual(result["previous_failure_count"], 0)

    def test_second_upsert_reports_merged_and_pre_merge_counters(self) -> None:
        # 첫 등록 후 usage 통계 누적
        _seed_manifest_tool(
            self.workspace,
            name="reused_tool",
            code="def run(arguments):\n    return {'ok': True}\n",
        )
        self.catalog.record_usage("reused_tool", success=True)
        self.catalog.record_usage("reused_tool", success=False)
        # 동일 이름으로 재등록 (예: 같은 이름의 새 generated tool 승인)
        result = self.catalog.upsert_with_diff(
            {"name": "reused_tool", "description": "재등록"}
        )
        self.assertTrue(result["merged"])
        self.assertEqual(result["previous_usage_count"], 2)
        self.assertEqual(result["previous_failure_count"], 1)

    def test_stats_are_preserved_when_re_upserting_without_them(self) -> None:
        # 기존 stats를 갖고 있는 entry를 새 metadata(stats 없음)로 덮어써도
        # _normalize의 fallback 덕에 stats가 보존되어야 한다 (회귀 방지)
        _seed_manifest_tool(
            self.workspace,
            name="preserved",
            code="def run(arguments):\n    return {'ok': True}\n",
        )
        self.catalog.record_usage("preserved", success=True)
        self.catalog.record_usage("preserved", success=True)
        before = next(t for t in self.catalog.list() if t["name"] == "preserved")
        self.assertEqual(before["usage_count"], 2)

        # 새 metadata에 usage_count 명시 X
        self.catalog.upsert({"name": "preserved", "description": "rewrite"})

        after = next(t for t in self.catalog.list() if t["name"] == "preserved")
        self.assertEqual(after["usage_count"], 2, "usage_count는 보존되어야 합니다")
        self.assertEqual(after["description"], "rewrite", "description은 갱신되어야 합니다")


class ToolApproveMergeOutputTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_first_approval_reports_no_merge(self) -> None:
        out = _seed_manifest_tool(
            self.workspace,
            name="brand_new",
            code="def run(arguments):\n    return {'ok': True}\n",
        )
        self.assertIn("manifest_merge", out)
        self.assertFalse(out["manifest_merge"]["merged"])
        self.assertEqual(out["manifest_merge"]["previous_usage_count"], 0)

    def test_second_approval_reports_merge_with_pre_counters(self) -> None:
        # 첫 승인 + 사용 통계 적재
        _seed_manifest_tool(
            self.workspace,
            name="repeated_approve",
            code="def run(arguments):\n    return {'v': 1}\n",
        )
        catalog = SkillCatalog(self.workspace / ".adaptive_agent" / "tools")
        catalog.record_usage("repeated_approve", success=True)
        catalog.record_usage("repeated_approve", success=True)
        catalog.record_usage("repeated_approve", success=False)

        # 같은 이름으로 다시 create→validate→approve (silent overwrite를 위해 overwrite=True)
        class _SilentLLM:
            def complete(self, _p):
                return '{"action":"respond","response":"ok"}'

        agent = AdaptiveAgent(
            config=AgentConfig(
                workspace_dir=self.workspace,
                tool_library_dir=self.workspace / ".adaptive_agent" / "tools",
                session_dir=self.workspace / ".adaptive_agent" / "sessions",
            ),
            llm_client=_SilentLLM(),
        )
        c2 = agent.run_tool(
            "tool_create",
            {
                "name": "repeated_approve",
                "description": "v2",
                "code": "def run(arguments):\n    return {'v': 2}\n",
                "overwrite": True,
            },
        )
        self.assertTrue(c2.success, c2.error)
        v2 = agent.run_tool("tool_validate", {"name": "repeated_approve"})
        self.assertTrue(v2.success, v2.error)
        approve2 = agent.run_tool("tool_approve", {"name": "repeated_approve"})

        self.assertTrue(approve2.success)
        merge_info = approve2.output["manifest_merge"]
        self.assertTrue(merge_info["merged"])
        self.assertEqual(merge_info["previous_usage_count"], 3)
        self.assertEqual(merge_info["previous_failure_count"], 1)

        # 그리고 manifest의 stats가 보존되었는지도 확인
        manifest = json.loads(
            (self.workspace / ".adaptive_agent" / "tools" / MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        entry = next(t for t in manifest["tools"] if t["name"] == "repeated_approve")
        self.assertEqual(entry["usage_count"], 3, "stats는 병합 후에도 보존")
        self.assertEqual(entry["failure_count"], 1)


class ManifestMergeEventTest(unittest.TestCase):
    """natural-language 경로에서 tool_approve가 일어나면 이벤트 발행."""

    def test_router_records_manifest_entry_merged_on_repeat_approve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            # 첫 승인 + 통계 누적
            _seed_manifest_tool(
                workspace,
                name="event_tool",
                code="def run(arguments):\n    return {'ok': True}\n",
            )
            catalog = SkillCatalog(workspace / ".adaptive_agent" / "tools")
            catalog.record_usage("event_tool", success=True)

            # 라우터 경로로 같은 이름 재승인 (LLM이 tool_approve 계획을 냄)
            class _SequenceLLM:
                def __init__(self, responses):
                    self.responses = list(responses)

                def complete(self, _p):
                    return self.responses.pop(0) if self.responses else '{"action":"respond","response":"end"}'

            agent = AdaptiveAgent(
                config=AgentConfig(
                    workspace_dir=workspace,
                    tool_library_dir=workspace / ".adaptive_agent" / "tools",
                    session_dir=workspace / ".adaptive_agent" / "sessions",
                ),
                llm_client=_SequenceLLM(
                    [
                        '{"action":"tool","tool_name":"tool_create","arguments":{"name":"event_tool","description":"v2","code":"def run(arguments):\\n    return {\\"v\\":2}\\n","overwrite":true}}',
                    ]
                ),
            )
            # tool_create → tool_validate → pending approval → resume(approve=True) → tool_approve
            result = agent.run("재승인 시나리오")
            self.assertEqual(result.action, "approval_required")
            approved = agent.resume(result.session_id, approve=True)

            self.assertEqual(approved.action, "tool")
            self.assertEqual(approved.tool_name, "tool_approve")
            merge_events = [e for e in approved.events if e.name == "manifest_entry_merged"]
            self.assertEqual(len(merge_events), 1, "manifest_entry_merged 이벤트가 정확히 한 번 발행되어야 함")
            self.assertEqual(merge_events[0].details["tool_name"], "event_tool")
            self.assertEqual(merge_events[0].details["previous_usage_count"], 1)


if __name__ == "__main__":
    unittest.main()
