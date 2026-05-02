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


if __name__ == "__main__":
    unittest.main()
