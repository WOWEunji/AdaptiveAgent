"""Multi-turn conversation session state and history persistence."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from adaptive_agent.state import Message


@dataclass
class PendingSave:
    """Code generated in one turn that the user may want to save as a skill."""

    turn_session_id: str  # UUID hex identifying the producing turn
    task: str
    suggested_name: str
    suggested_desc: str
    code: str


@dataclass
class ConversationSession:
    """State shared across all turns of one interactive session."""

    session_id: str = field(default_factory=lambda: uuid4().hex)
    history: list[Message] = field(default_factory=list)
    pending_saves: list[PendingSave] = field(default_factory=list)
    pending_action: dict | None = field(default=None)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )


class ConversationHistoryStore:
    """대화 히스토리를 {session_dir}/history/ 에 파일로 저장/복원한다.

    SessionStore(HITL)와 분리된 서브디렉터리를 사용해 충돌을 방지한다.
    저장 실패는 stderr 경고만 출력하고 예외를 전파하지 않는다.
    """

    def __init__(self, session_dir: Path) -> None:
        self._dir = session_dir / "history"

    def save(self, session: ConversationSession) -> None:
        """session.history를 JSON 파일로 저장. 메시지가 없으면 파일을 만들지 않는다."""
        if not session.history:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._dir / f"{session.session_id}.json"
            payload = {
                "session_id": session.session_id,
                "created_at": session.created_at,
                "messages": [
                    {"role": m.role, "content": m.content}
                    for m in session.history
                ],
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"[경고] 히스토리 저장 실패: {exc}", file=sys.stderr)

    def load_latest(self) -> list[Message]:
        """mtime 기준 가장 최근 히스토리 파일을 로드. 없으면 빈 리스트."""
        if not self._dir.exists():
            return []
        files = sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return []
        return self._load_file(files[0])

    def load(self, session_id: str) -> list[Message]:
        """session_id로 특정 히스토리 파일을 로드. 없으면 빈 리스트."""
        path = self._dir / f"{session_id}.json"
        if not path.exists():
            return []
        return self._load_file(path)

    def _load_file(self, path: Path) -> list[Message]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [
                Message(role=m["role"], content=m["content"])
                for m in data.get("messages", [])
                if m.get("role") in ("user", "assistant")
            ]
        except Exception:  # noqa: BLE001
            return []
