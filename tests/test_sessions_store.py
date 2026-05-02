"""SessionStore 단위 테스트.

보호하는 계약:
- session_id는 정확히 32자리 hex만 허용 (path traversal 차단 포함)
- pending 상태만 resume 가능, close 후에는 실패해야 함
- 디스크 손상(잘못된 JSON, dict 아닌 루트) 시 ValueError로 명확히 실패
- 비밀(코드, SECRET 등)이 페이로드에 직렬화되지 않음
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from adaptive_agent.sessions import SessionStore
from adaptive_agent.state import AgentState


def _make_state(*, session_id: str = "a" * 32, user_task: str = "task") -> AgentState:
    state = AgentState(session_id=session_id, user_task=user_task)
    return state


class SessionStorePathTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(sessions_dir=Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_session_id_must_be_exactly_32_hex_chars(self) -> None:
        invalid_ids = [
            "",
            "short",
            "a" * 31,           # 31자
            "a" * 33,           # 33자
            "g" * 32,           # 'g'는 hex 아님
            "../../../etc/passwd",
            "/absolute/path",
            "..",
            "0" * 32 + ".json",
            "0" * 32 + "/x",
            "../" + "a" * 32,
        ]
        for bad in invalid_ids:
            with self.subTest(session_id=bad):
                with self.assertRaises(ValueError):
                    self.store._path_for(bad)

    def test_session_id_accepts_lower_and_upper_hex(self) -> None:
        for sid in ("a" * 32, "A" * 32, "0123456789abcdef0123456789ABCDEF"):
            with self.subTest(session_id=sid):
                path = self.store._path_for(sid)
                self.assertEqual(path.parent, Path(self.tmp.name).resolve())


class SessionStoreLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(sessions_dir=Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_save_then_load_returns_pending_payload(self) -> None:
        state = _make_state(user_task="원문 보존")
        state.last_tool_name = "ask_human"
        output = {"status": "pending_human_input", "questions": ["계속할까요?"]}

        saved = self.store.save_pending(state, output)
        loaded = self.store.load_pending(state.session_id)

        self.assertEqual(saved["status"], "pending")
        self.assertEqual(loaded["session_id"], state.session_id)
        self.assertEqual(loaded["user_task"], "원문 보존")
        self.assertEqual(loaded["pending_type"], "pending_human_input")

    def test_close_blocks_subsequent_load(self) -> None:
        state = _make_state()
        self.store.save_pending(state, {"status": "pending"})

        self.store.close(state.session_id, status="resolved")

        with self.assertRaises(ValueError):
            self.store.load_pending(state.session_id)

    def test_load_missing_session_raises_value_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.store.load_pending("a" * 32)
        self.assertIn("a" * 32, str(ctx.exception))

    def test_load_corrupt_json_raises_value_error(self) -> None:
        sid = "b" * 32
        (Path(self.tmp.name) / f"{sid}.json").write_text("not json{{{", encoding="utf-8")

        with self.assertRaises(ValueError):
            self.store.load_pending(sid)

    def test_load_non_dict_root_raises_value_error(self) -> None:
        sid = "c" * 32
        (Path(self.tmp.name) / f"{sid}.json").write_text("[1, 2, 3]", encoding="utf-8")

        with self.assertRaises(ValueError):
            self.store.load_pending(sid)

    def test_close_on_non_pending_status_fails(self) -> None:
        sid = "d" * 32
        (Path(self.tmp.name) / f"{sid}.json").write_text(
            json.dumps({"status": "resolved", "session_id": sid}),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            self.store.close(sid, status="closed")

    def test_secrets_in_arguments_are_not_serialized(self) -> None:
        state = _make_state()
        state.current_plan = {
            "action": "tool",
            "tool_name": "tool_create",
            "arguments": {
                "name": "secret_tool",
                "description": "탈출 시도",
                "code": "SECRET=sk-12345; def run(arguments): return SECRET",
                "api_key": "sk-very-private-key",
            },
        }

        self.store.save_pending(state, {"status": "approval_required"})
        raw = (Path(self.tmp.name) / f"{state.session_id}.json").read_text(encoding="utf-8")

        self.assertNotIn("def run", raw, "코드 본문은 세션 페이로드에 직렬화되면 안 됩니다")
        self.assertNotIn("sk-very-private-key", raw, "비밀 키 형태 인자는 페이로드에 들어가면 안 됩니다")
        self.assertNotIn("SECRET=", raw, "SECRET= 형태 문자열도 페이로드에 들어가면 안 됩니다")
        # 화이트리스트(name/description)는 보존
        self.assertIn("secret_tool", raw)
        self.assertIn("탈출 시도", raw)


class InputVariationContractTest(unittest.TestCase):
    """save_pending이 다양한 입력 형태를 안전하게 다루는지 확인."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(sessions_dir=Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_save_handles_various_user_task_inputs(self) -> None:
        cases = [
            ("한국어", "한국어 작업입니다"),
            ("이모지", "🎉 ship it 🚀"),
            ("긴 입력", "x" * 50_000),
            ("혼합 공백", "  앞 뒤  공백 \t\n 보존  "),
            ("JSON처럼 보이는 텍스트", '{"action":"tool"}'),
            ("따옴표 혼합", "\"양쪽\" '단일' 따옴표"),
        ]
        for label, task in cases:
            with self.subTest(case=label):
                state = _make_state(session_id=f"{abs(hash(label)):032x}"[:32], user_task=task)
                self.store.save_pending(state, {"status": "pending"})
                loaded = self.store.load_pending(state.session_id)
                self.assertEqual(loaded["user_task"], task, "원문 task는 손실 없이 round-trip 되어야 합니다")

    def test_save_handles_non_dict_outputs(self) -> None:
        state = _make_state(session_id="e" * 32, user_task="t")
        self.store.save_pending(state, "string output value")

        loaded = self.store.load_pending(state.session_id)
        # 비-dict 출력은 status="pending" 폴백 (문자열 그대로 또는 dict 폴백)
        self.assertIn(loaded["pending_output"], ["string output value", {"status": "pending"}])


class SessionCleanupTest(unittest.TestCase):
    """cleanup_expired의 TTL/cap 정책 검증."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.store = SessionStore(sessions_dir=self.dir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed(self, session_id: str, *, days_old: int) -> None:
        """Write a session payload with updated_at days_old days in the past."""

        from datetime import datetime, timedelta, timezone

        ts = datetime.now(timezone.utc) - timedelta(days=days_old)
        payload = {
            "session_id": session_id,
            "status": "pending",
            "user_task": f"task-{session_id[:6]}",
            "updated_at": ts.isoformat().replace("+00:00", "Z"),
        }
        (self.dir / f"{session_id}.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_cleanup_on_empty_dir_returns_no_deletions(self) -> None:
        result = self.store.cleanup_expired()
        self.assertEqual(result, {"deleted": [], "reasons": {}})

    def test_ttl_deletes_old_sessions(self) -> None:
        self._seed("a" * 32, days_old=45)  # past TTL
        self._seed("b" * 32, days_old=10)  # within TTL

        result = self.store.cleanup_expired(max_age_days=30, max_count=100)

        self.assertIn("a" * 32, result["deleted"])
        self.assertNotIn("b" * 32, result["deleted"])
        self.assertEqual(result["reasons"]["a" * 32], "ttl")
        self.assertFalse((self.dir / ("a" * 32 + ".json")).exists())
        self.assertTrue((self.dir / ("b" * 32 + ".json")).exists())

    def test_cap_deletes_oldest_first(self) -> None:
        # 5 fresh sessions, cap to 2 → oldest 3 deleted.
        for i in range(5):
            self._seed(f"{i}" * 32, days_old=i)  # i=0 newest, i=4 oldest

        result = self.store.cleanup_expired(max_age_days=365, max_count=2)

        self.assertEqual(len(result["deleted"]), 3)
        # Three oldest (days_old=4,3,2) should be deleted.
        for sid in ("4" * 32, "3" * 32, "2" * 32):
            self.assertIn(sid, result["deleted"])
            self.assertEqual(result["reasons"][sid], "cap")
        # Two newest survive.
        self.assertTrue((self.dir / ("0" * 32 + ".json")).exists())
        self.assertTrue((self.dir / ("1" * 32 + ".json")).exists())

    def test_ttl_takes_precedence_over_cap(self) -> None:
        # Mix: 2 ancient (TTL) + 3 fresh (within cap).
        self._seed("a" * 32, days_old=60)
        self._seed("b" * 32, days_old=50)
        self._seed("0" * 32, days_old=0)
        self._seed("1" * 32, days_old=1)
        self._seed("2" * 32, days_old=2)

        result = self.store.cleanup_expired(max_age_days=30, max_count=100)

        self.assertEqual(set(result["deleted"]), {"a" * 32, "b" * 32})
        self.assertTrue(all(r == "ttl" for r in result["reasons"].values()))

    def test_corrupt_files_are_deleted(self) -> None:
        sid = "c" * 32
        (self.dir / f"{sid}.json").write_text("not json{{{", encoding="utf-8")

        result = self.store.cleanup_expired()

        self.assertIn(sid, result["deleted"])
        self.assertEqual(result["reasons"][sid], "corrupt")
        self.assertFalse((self.dir / f"{sid}.json").exists())

    def test_files_without_session_id_pattern_are_left_alone(self) -> None:
        (self.dir / "not-a-session.json").write_text("{}", encoding="utf-8")
        (self.dir / "README.txt").write_text("docs", encoding="utf-8")

        result = self.store.cleanup_expired()

        self.assertEqual(result["deleted"], [])
        self.assertTrue((self.dir / "not-a-session.json").exists())
        self.assertTrue((self.dir / "README.txt").exists())


class AgentInitCleanupTest(unittest.TestCase):
    """AdaptiveAgent.__init__이 cleanup을 옵션대로 호출하는지."""

    def test_init_runs_cleanup_when_enabled(self) -> None:
        from datetime import datetime, timedelta, timezone

        from adaptive_agent.agent import AdaptiveAgent
        from adaptive_agent.config import AgentConfig

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            session_dir = workspace / ".adaptive_agent" / "sessions"
            session_dir.mkdir(parents=True)
            old_id = "f" * 32
            ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat().replace("+00:00", "Z")
            (session_dir / f"{old_id}.json").write_text(
                json.dumps({"session_id": old_id, "status": "pending", "updated_at": ts}),
                encoding="utf-8",
            )

            class _StubLLM:
                def complete(self, _p):  # noqa: D401
                    return "ok"

            agent = AdaptiveAgent(
                config=AgentConfig(
                    workspace_dir=workspace,
                    tool_library_dir=workspace / ".adaptive_agent" / "tools",
                    session_dir=session_dir,
                ),
                llm_client=_StubLLM(),
            )

            self.assertIn(old_id, agent._cleanup_summary["deleted"])
            self.assertFalse((session_dir / f"{old_id}.json").exists())

    def test_init_skips_cleanup_when_disabled(self) -> None:
        from datetime import datetime, timedelta, timezone

        from adaptive_agent.agent import AdaptiveAgent
        from adaptive_agent.config import AgentConfig

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            session_dir = workspace / ".adaptive_agent" / "sessions"
            session_dir.mkdir(parents=True)
            old_id = "9" * 32
            ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat().replace("+00:00", "Z")
            (session_dir / f"{old_id}.json").write_text(
                json.dumps({"session_id": old_id, "status": "pending", "updated_at": ts}),
                encoding="utf-8",
            )

            class _StubLLM:
                def complete(self, _p):  # noqa: D401
                    return "ok"

            agent = AdaptiveAgent(
                config=AgentConfig(
                    workspace_dir=workspace,
                    tool_library_dir=workspace / ".adaptive_agent" / "tools",
                    session_dir=session_dir,
                    session_cleanup_enabled=False,
                ),
                llm_client=_StubLLM(),
            )

            self.assertEqual(agent._cleanup_summary["deleted"], [])
            self.assertTrue((session_dir / f"{old_id}.json").exists())


if __name__ == "__main__":
    unittest.main()
