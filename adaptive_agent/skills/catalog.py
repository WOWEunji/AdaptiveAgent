"""Manifest-backed skill catalog for generated tools."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST_FILENAME = "manifest.json"
SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_STALE_REASONS = {
    "missing_file": "manifest 항목이 가리키는 .py 파일이 없음",
    "hash_mismatch": "파일 내용이 manifest의 file_hash와 일치하지 않음",
    "missing_hash": "manifest에 file_hash가 없음 (legacy entry)",
    "missing_path": "manifest 항목에 file_path가 없음",
}


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

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        mode: str = "keyword",
        embedder: object | None = None,
        threshold: float = 0.4,
    ) -> list[dict[str, Any]]:
        """Return ranked approved tool metadata for a query.

        ``mode`` options:
        - ``keyword`` (default): token weighted ranking (existing behavior).
        - ``semantic``: cosine similarity against stored embeddings; entries
          without an embedding (or with model mismatch) are skipped. Requires
          an ``embedder`` instance.
        - ``auto``: semantic when an embedder is provided, else keyword.
        """

        normalized_mode = mode.lower().strip()
        if normalized_mode == "auto":
            normalized_mode = "semantic" if embedder is not None else "keyword"

        if normalized_mode == "semantic":
            return self._search_semantic(query, top_k=top_k, embedder=embedder, threshold=threshold)

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

    def _search_semantic(
        self,
        query: str,
        *,
        top_k: int,
        embedder: object | None,
        threshold: float,
    ) -> list[dict[str, Any]]:
        from adaptive_agent.skills.embedding import cosine_similarity

        if embedder is None:
            return []
        query_vec = embedder.embed(query)  # type: ignore[union-attr]
        if not query_vec:
            return []
        active_model = getattr(embedder, "model_id", "")
        scored: list[dict[str, Any]] = []
        for tool in self.list():
            name = str(tool.get("name") or "")
            if not name:
                continue
            entry_vec = tool.get("embedding")
            entry_model = str(tool.get("embedding_model") or "")
            if not isinstance(entry_vec, list) or entry_model != active_model:
                continue
            score = cosine_similarity([float(x) for x in query_vec], [float(x) for x in entry_vec])
            if score < threshold:
                continue
            scored.append({**tool, "score": score})
        scored.sort(key=lambda item: (-float(item.get("score", 0)), str(item.get("name", ""))))
        return scored[: max(top_k, 0)]

    def attach_embedding(self, name: str, vector: list[float], *, embedding_model: str) -> dict[str, Any] | None:
        """Persist an embedding vector against a manifest entry by name."""

        existing = self._find_existing(name)
        if not existing:
            return None
        existing["embedding"] = list(vector)
        existing["embedding_model"] = embedding_model
        existing["updated_at"] = _utc_now()
        return self.upsert(existing)

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

    def record_usage(self, name: str, *, success: bool) -> dict[str, Any] | None:
        """Increment ``usage_count`` and (on failure) ``failure_count`` for ``name``.

        Returns the updated metadata if the tool exists in the manifest, otherwise
        ``None``. Missing tools are intentionally a no-op so that calling code
        (e.g., the executor wired to *every* tool run) does not have to filter
        out builtins or unregistered names beforehand.
        """

        existing = self._find_existing(name)
        if not existing:
            return None
        existing["usage_count"] = int(existing.get("usage_count", 0)) + 1
        if not success:
            existing["failure_count"] = int(existing.get("failure_count", 0)) + 1
        existing["updated_at"] = _utc_now()
        return self.upsert(existing)

    def find_stale_entries(self) -> list[dict[str, Any]]:
        """Return manifest entries whose backing file is missing or tampered.

        Each entry is ``{"name": ..., "reason": <code>, "detail": <message>}``.
        ``reason`` codes:

        - ``missing_path`` — manifest 항목에 file_path가 없음
        - ``missing_file`` — manifest 항목이 가리키는 .py 파일이 없음
        - ``missing_hash`` — manifest에 file_hash가 없음 (legacy entry)
        - ``hash_mismatch`` — 파일 내용이 manifest의 file_hash와 일치하지 않음
        """

        stale: list[dict[str, Any]] = []
        for tool in self.list():
            name = str(tool.get("name") or "")
            if not name:
                continue
            file_path = str(tool.get("file_path") or "")
            if not file_path:
                stale.append({"name": name, "reason": "missing_path", "detail": _STALE_REASONS["missing_path"]})
                continue
            path = Path(file_path)
            if not path.exists():
                stale.append({"name": name, "reason": "missing_file", "detail": _STALE_REASONS["missing_file"]})
                continue
            expected_hash = str(tool.get("file_hash") or "")
            if not expected_hash:
                stale.append({"name": name, "reason": "missing_hash", "detail": _STALE_REASONS["missing_hash"]})
                continue
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                stale.append({"name": name, "reason": "hash_mismatch", "detail": _STALE_REASONS["hash_mismatch"]})
        return stale

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
            "embedding": (
                metadata.get("embedding")
                if isinstance(metadata.get("embedding"), list)
                else existing.get("embedding")
            ),
            "embedding_model": str(
                metadata.get("embedding_model")
                or existing.get("embedding_model")
                or ""
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
