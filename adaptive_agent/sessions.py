"""File-backed session snapshots for HITL resume."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from adaptive_agent.state import AgentState

_SESSION_ID_PATTERN = re.compile(r"^[A-Fa-f0-9]{32}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class SessionStore:
    """Store and load minimal pending HITL session snapshots."""

    sessions_dir: Path

    def save_pending(
        self,
        state: AgentState,
        output: Any,
        *,
        resume_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist the minimum state needed to resume a pending request."""

        payload = {
            "session_id": state.session_id,
            "status": "pending",
            "pending_type": _pending_type(output),
            "user_task": state.user_task,
            "current_plan": _safe_plan(state.current_plan),
            "resume_plan": _safe_plan(resume_plan or {}),
            "last_tool_name": state.last_tool_name,
            "last_tool_arguments": {},
            "last_tool_result": _safe_tool_result(state.last_tool_result),
            "reflections": state.reflections,
            "pending_output": _safe_pending_output(output),
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._path_for(state.session_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def load_pending(self, session_id: str) -> dict[str, Any]:
        """Load a pending session snapshot with strict path and status checks."""

        path = self._path_for(session_id)
        if not path.exists():
            raise ValueError(f"세션을 찾을 수 없습니다: {session_id}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"세션 파일을 읽을 수 없습니다: {session_id}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"세션 파일 형식이 올바르지 않습니다: {session_id}")
        if payload.get("status") != "pending":
            raise ValueError(f"pending 상태의 세션만 재개할 수 있습니다: {session_id}")
        return payload

    def close(self, session_id: str, status: str) -> None:
        """Mark a pending session as closed to prevent duplicate resume."""

        payload = self.load_pending(session_id)
        payload["status"] = status
        payload["updated_at"] = _utc_now()
        self._path_for(session_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def cleanup(self, *, ttl_hours: int, max_count: int) -> list[str]:
        """Delete expired and excess session files.

        Applies TTL eviction first (based on ``updated_at`` / ``created_at``),
        then enforces the count cap by removing the oldest remaining files.
        Returns the list of deleted ``session_id`` values.
        """

        if not self.sessions_dir.exists():
            return []

        session_files = list(self.sessions_dir.glob("*.json"))
        deleted: list[str] = []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)

        # TTL pass: delete files whose last-touch timestamp is before cutoff
        survivors: list[tuple[datetime, Path]] = []
        for path in session_files:
            ts = self._session_timestamp(path)
            if ts is not None and ts < cutoff:
                try:
                    path.unlink(missing_ok=True)
                    deleted.append(path.stem)
                except OSError:
                    pass
            else:
                survivors.append((ts or datetime.min.replace(tzinfo=timezone.utc), path))

        # Count cap pass: if still over limit, remove oldest by timestamp
        if len(survivors) > max_count:
            survivors.sort(key=lambda item: item[0])
            for _, path in survivors[: len(survivors) - max_count]:
                try:
                    path.unlink(missing_ok=True)
                    deleted.append(path.stem)
                except OSError:
                    pass

        return deleted

    @staticmethod
    def _session_timestamp(path: Path) -> datetime | None:
        """Return the ``updated_at`` / ``created_at`` timestamp of a session file."""

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        for field in ("updated_at", "created_at"):
            raw = payload.get(field)
            if isinstance(raw, str):
                try:
                    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    return dt.astimezone(timezone.utc)
                except ValueError:
                    continue
        return None

    def _path_for(self, session_id: str) -> Path:
        if not _SESSION_ID_PATTERN.match(session_id):
            raise ValueError("session_id는 32자리 hex 문자열이어야 합니다.")
        sessions_dir = self.sessions_dir.resolve()
        candidate = (sessions_dir / f"{session_id}.json").resolve()
        if candidate.parent != sessions_dir:
            raise ValueError("세션 경로가 허용 범위를 벗어났습니다.")
        return candidate


def _pending_type(output: Any) -> str:
    if isinstance(output, dict):
        status = output.get("status")
        if isinstance(status, str):
            return status
    return "pending"


def _safe_plan(plan: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, dict):
        return {}
    safe: dict[str, Any] = {
        "action": plan.get("action"),
        "tool_name": plan.get("tool_name"),
    }
    arguments = plan.get("arguments")
    if isinstance(arguments, dict):
        safe["arguments"] = {
            key: value
            for key, value in arguments.items()
            if key in {"name", "description"}
        }
    return safe


def _safe_tool_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    return {
        "success": result.get("success"),
        "error": result.get("error"),
        "output_status": _pending_type(result.get("output")),
    }


def _safe_pending_output(output: Any) -> Any:
    if not isinstance(output, dict):
        return output if isinstance(output, str) else {"status": "pending"}
    safe = {"status": output.get("status")}
    if "questions" in output:
        safe["questions"] = output.get("questions")
    if "options" in output:
        safe["options"] = output.get("options")
    if "risk_level" in output:
        safe["risk_level"] = output.get("risk_level")
    if "plan" in output:
        safe["plan_summary"] = str(output.get("plan"))[:500]
    return safe
