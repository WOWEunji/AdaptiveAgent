"""Manifest-backed skill catalog for generated tools."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST_FILENAME = "manifest.json"
SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class SkillCatalog:
    """Manifest-backed index for approved generated tools."""

    tool_library: Path
    manifest_filename: str = MANIFEST_FILENAME
    _skills: list[dict[str, Any]] = field(default_factory=list, init=False)

    @property
    def path(self) -> Path:
        return self.tool_library / self.manifest_filename

    def list(self) -> list[dict[str, Any]]:
        """Return approved tool metadata from the manifest."""

        return list(self._load().get("tools", []))

    def search(self, query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
        """Return ranked approved tool metadata for a query."""

        query_tokens = _tokens(query)
        ranked: list[dict[str, Any]] = []
        seen: set[str] = set()
        for tool in self.list():
            name = str(tool.get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            score = _score_tool(tool, query, query_tokens)
            if query_tokens and score <= 0:
                continue
            ranked.append({**tool, "score": score})
        ranked.sort(key=lambda item: (-float(item.get("score", 0)), str(item.get("name", ""))))
        return ranked[: max(top_k, 0)]

    def upsert(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """Insert or replace approved tool metadata by name."""

        normalized = self._normalize(metadata)
        payload = self._load()
        tools = [tool for tool in payload.get("tools", []) if tool.get("name") != normalized["name"]]
        tools.append(normalized)
        payload["schema_version"] = SCHEMA_VERSION
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
        tools: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_tool in payload.get("tools", []):
            if not isinstance(raw_tool, dict):
                continue
            normalized = self._normalize(raw_tool, use_existing=False)
            name = normalized["name"]
            if not name or name in seen:
                continue
            seen.add(name)
            tools.append(normalized)
        return {"schema_version": payload.get("schema_version") or SCHEMA_VERSION, "tools": tools}

    def _normalize(self, metadata: dict[str, Any], *, use_existing: bool = True) -> dict[str, Any]:
        name = str(metadata.get("name") or "")
        existing = self._find_existing(name) if use_existing else {}
        return {
            "name": name,
            "description": str(metadata.get("description") or existing.get("description") or ""),
            "category": str(metadata.get("category") or existing.get("category") or "generated"),
            "tags": _normalize_tags(metadata.get("tags") or existing.get("tags") or []),
            "file_path": str(metadata.get("file_path") or metadata.get("path") or existing.get("file_path") or ""),
            "file_hash": str(metadata.get("file_hash") or existing.get("file_hash") or ""),
            "parameters": metadata.get("parameters") if isinstance(metadata.get("parameters"), dict) else existing.get("parameters", {}),
            "returns": metadata.get("returns") if isinstance(metadata.get("returns"), dict) else existing.get("returns", {}),
            "validation_status": str(
                metadata.get("validation_status")
                or metadata.get("status")
                or existing.get("validation_status")
                or "unverified"
            ),
            "approval_status": str(
                metadata.get("approval_status")
                or ("approved" if metadata.get("approved") is True else "")
                or existing.get("approval_status")
                or "unapproved"
            ),
            "created_at": str(metadata.get("created_at") or existing.get("created_at") or _utc_now()),
            "updated_at": str(metadata.get("updated_at") or _utc_now()),
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


def _normalize_tags(raw_tags: object) -> list[str]:
    if not isinstance(raw_tags, list):
        return []
    return sorted({str(tag) for tag in raw_tags if str(tag)})


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in "".join(char.casefold() if char.isalnum() else " " for char in text).split()
        if token
    }


def _score_tool(tool: dict[str, Any], query: str, query_tokens: set[str]) -> float:
    if not query_tokens:
        return 1.0
    haystack = " ".join(
        [
            str(tool.get("name") or ""),
            str(tool.get("description") or ""),
            str(tool.get("category") or ""),
            " ".join(str(tag) for tag in tool.get("tags", []) if isinstance(tag, str)),
        ]
    ).casefold()
    name_tokens = _tokens(str(tool.get("name") or ""))
    description_tokens = _tokens(str(tool.get("description") or ""))
    category_tokens = _tokens(str(tool.get("category") or ""))
    tag_tokens = _tokens(" ".join(str(tag) for tag in tool.get("tags", []) if isinstance(tag, str)))
    parameter_tokens = _tokens(" ".join(str(key) for key in (tool.get("parameters") or {}).keys()))
    score = (
        4 * len(query_tokens & name_tokens)
        + 2 * len(query_tokens & description_tokens)
        + 1.5 * len(query_tokens & tag_tokens)
        + len(query_tokens & category_tokens)
        + len(query_tokens & parameter_tokens)
    )
    if query.casefold() in haystack:
        score += 1
    return score
