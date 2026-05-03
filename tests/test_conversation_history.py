"""ConversationHistoryStore 단위 테스트.

보호하는 계약:
- save → load_latest 라운드트립: role/content 보존
- save → load(session_id) 특정 세션 복원
- 메시지 없는 session은 파일을 생성하지 않음
- 손상된 JSON / 존재하지 않는 파일은 빈 리스트 반환 (예외 없음)
- role이 user/assistant 아닌 메시지는 필터링
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from adaptive_agent.conversation import ConversationHistoryStore, ConversationSession
from adaptive_agent.state import Message


def _make_session(*pairs: tuple[str, str]) -> ConversationSession:
    """(role, content) 쌍으로 history가 채워진 ConversationSession을 반환한다."""
    session = ConversationSession()
    session.history = [Message(role=r, content=c) for r, c in pairs]
    return session


class HistoryRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ConversationHistoryStore(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_save_and_load_latest_preserves_messages(self) -> None:
        session = _make_session(("user", "안녕"), ("assistant", "안녕하세요"))
        self.store.save(session)

        loaded = self.store.load_latest()
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0].role, "user")
        self.assertEqual(loaded[0].content, "안녕")
        self.assertEqual(loaded[1].role, "assistant")
        self.assertEqual(loaded[1].content, "안녕하세요")

    def test_save_and_load_by_session_id(self) -> None:
        session = _make_session(("user", "hello"), ("assistant", "world"))
        self.store.save(session)

        loaded = self.store.load(session.session_id)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0].content, "hello")

    def test_load_latest_returns_most_recent(self) -> None:
        s1 = _make_session(("user", "first"))
        self.store.save(s1)
        s2 = _make_session(("user", "second"))
        self.store.save(s2)

        loaded = self.store.load_latest()
        # 마지막으로 저장한 s2가 가장 최근이어야 함
        self.assertEqual(loaded[0].content, "second")

    def test_empty_history_does_not_create_file(self) -> None:
        session = ConversationSession()
        self.store.save(session)

        history_dir = Path(self.tmp.name) / "history"
        self.assertFalse(history_dir.exists())

    def test_load_latest_missing_dir_returns_empty(self) -> None:
        result = self.store.load_latest()
        self.assertEqual(result, [])

    def test_load_missing_session_id_returns_empty(self) -> None:
        result = self.store.load("nonexistent_session_id")
        self.assertEqual(result, [])

    def test_load_corrupt_json_returns_empty(self) -> None:
        history_dir = Path(self.tmp.name) / "history"
        history_dir.mkdir()
        (history_dir / "bad.json").write_text("not json{{", encoding="utf-8")

        result = self.store.load_latest()
        self.assertEqual(result, [])

    def test_unknown_roles_are_filtered(self) -> None:
        session = ConversationSession()
        session.history = [
            Message(role="user", content="keep"),
            Message(role="system", content="drop"),
            Message(role="assistant", content="keep too"),
        ]
        self.store.save(session)

        loaded = self.store.load(session.session_id)
        self.assertEqual(len(loaded), 2)
        roles = {m.role for m in loaded}
        self.assertNotIn("system", roles)

    def test_unicode_content_survives_roundtrip(self) -> None:
        session = _make_session(("user", "한국어 테스트 🎉"), ("assistant", "응답입니다"))
        self.store.save(session)

        loaded = self.store.load(session.session_id)
        self.assertEqual(loaded[0].content, "한국어 테스트 🎉")

    def test_save_failure_does_not_raise(self) -> None:
        """저장 실패 시 예외가 전파되지 않아야 한다."""
        # history_dir를 파일로 만들어 mkdir 실패를 유발
        history_path = Path(self.tmp.name) / "history"
        history_path.write_text("block", encoding="utf-8")

        session = _make_session(("user", "test"))
        # 예외 없이 완료되어야 함
        self.store.save(session)


if __name__ == "__main__":
    unittest.main()
