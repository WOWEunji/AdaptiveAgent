"""Manifest-backed skill catalog for generated tools."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST_FILENAME = "manifest.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class SkillCatalog:
    """`.adaptive_agent/tools/manifest.json`을 단일 스킬 인덱스로 관리합니다."""

    tool_library: Path
    manifest_filename: str = MANIFEST_FILENAME
    _skills: list[dict[str, Any]] = field(default_factory=list, init=False)

    @property
    def path(self) -> Path:
        return self.tool_library / self.manifest_filename

    def list(self) -> list[dict[str, Any]]:
        """현재 manifest의 스킬 목록을 반환합니다."""

        return list(self._load().get("tools", []))

    def upsert(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """스킬 metadata를 기본 필드로 정규화한 뒤 이름 기준으로 추가/갱신합니다."""

        normalized = self._normalize(metadata)
        payload = self._load()
        tools = [tool for tool in payload.get("tools", []) if tool.get("name") != normalized["name"]]
        tools.append(normalized)
        payload["tools"] = sorted(tools, key=lambda item: str(item.get("name", "")))
        self.tool_library.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return normalized

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"tools": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"tools": []}
        if not isinstance(payload, dict) or not isinstance(payload.get("tools", []), list):
            return {"tools": []}
        return payload

    def _normalize(self, metadata: dict[str, Any]) -> dict[str, Any]:
        name = str(metadata.get("name") or "")
        existing = self._find_existing(name)
        return {
            "name": name,
            "description": str(metadata.get("description") or existing.get("description") or ""),
            "file_path": str(metadata.get("file_path") or metadata.get("path") or existing.get("file_path") or ""),
            "parameters": metadata.get("parameters") if isinstance(metadata.get("parameters"), dict) else existing.get("parameters", {}),
            "returns": metadata.get("returns") if isinstance(metadata.get("returns"), dict) else existing.get("returns", {}),
            "validation_status": str(
                metadata.get("validation_status")
                or metadata.get("status")
                or existing.get("validation_status")
                or "unverified"
            ),
            "created_at": str(metadata.get("created_at") or existing.get("created_at") or _utc_now()),
            "usage_count": int(metadata.get("usage_count", existing.get("usage_count", 0))),
            "failure_count": int(metadata.get("failure_count", existing.get("failure_count", 0))),
            "reflections": (
                metadata.get("reflections")
                if isinstance(metadata.get("reflections"), list)
                else existing.get("reflections", [])
            ),
        }

    def _find_existing(self, name: str) -> dict[str, Any]:
        for tool in self._load().get("tools", []):
            if isinstance(tool, dict) and tool.get("name") == name:
                return tool
        return {}
