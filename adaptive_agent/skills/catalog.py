"""Manifest-backed skill catalog for generated tools."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

EmbeddingFn = Callable[[str], list[float]]


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
    embedding_fn: EmbeddingFn | None = None
    _skills: list[dict[str, Any]] = field(default_factory=list, init=False)

    @property
    def path(self) -> Path:
        return self.tool_library / self.manifest_filename

    def list(self) -> list[dict[str, Any]]:
        """Return approved tool metadata from the manifest."""

        return list(self._load().get("tools", []))

    def search(self, query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
        """Return ranked approved tool metadata for a query.

        When ``embedding_fn`` is set and stored embeddings exist, uses cosine
        similarity for ranking. Falls back to keyword scoring otherwise.
        """

        tools = self.list()
        if self.embedding_fn is not None:
            try:
                query_vec = self.embedding_fn(query)
                ranked = _rank_by_embedding(tools, query_vec)
                if ranked:
                    return ranked[: max(top_k, 0)]
            except Exception:
                pass  # embedding call failed — fall through to keyword search

        query_tokens = _tokens(query)
        ranked_kw: list[dict[str, Any]] = []
        seen: set[str] = set()
        for tool in tools:
            name = str(tool.get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            score = _score_tool(tool, query, query_tokens)
            if query_tokens and score <= 0:
                continue
            ranked_kw.append({**tool, "score": score})
        ranked_kw.sort(key=lambda item: (-float(item.get("score", 0)), str(item.get("name", ""))))
        return ranked_kw[: max(top_k, 0)]

    def upsert(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """Insert or replace approved tool metadata by name.

        When replacing an existing entry, ``usage_count``, ``failure_count``,
        and ``created_at`` are preserved (via :meth:`_normalize`) so accumulated
        stats survive re-approval. When ``embedding_fn`` is set, generates and
        stores a fresh embedding vector for the tool's description.
        """

        normalized = self._normalize(metadata)
        if self.embedding_fn is not None:
            text = f"{normalized['name']} {normalized['description']}"
            try:
                normalized["embedding"] = self.embedding_fn(text)
            except Exception:
                pass  # embedding failure must not block approval
        payload = self._load()
        tools = [tool for tool in payload.get("tools", []) if tool.get("name") != normalized["name"]]
        tools.append(normalized)
        payload["schema_version"] = SCHEMA_VERSION
        payload["tools"] = sorted(tools, key=lambda item: str(item.get("name", "")))
        self.tool_library.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return normalized

    def delete(self, name: str) -> bool:
        """Remove name from the manifest and persist. Returns True if removed, False if not found."""

        data = self._load()
        tools = data.get("tools", [])
        before = len(tools)
        data["tools"] = [t for t in tools if t.get("name") != name]
        if len(data["tools"]) == before:
            return False
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

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
            "created_at": str(metadata.get("created_at") or existing.get("created_at") or _utc_now()),
            "updated_at": str(metadata.get("updated_at") or _utc_now()),
            "usage_count": int(metadata.get("usage_count", existing.get("usage_count", 0))),
            "failure_count": int(metadata.get("failure_count", existing.get("failure_count", 0))),
            "reflections": (
                metadata.get("reflections")
                if isinstance(metadata.get("reflections"), list)
                else existing.get("reflections", [])
            ),
            "embedding": (
                metadata.get("embedding")
                if isinstance(metadata.get("embedding"), list)
                else existing.get("embedding")
            ),
        }

    def _find_existing(self, name: str) -> dict[str, Any]:
        for tool in self._load().get("tools", []):
            if isinstance(tool, dict) and tool.get("name") == name:
                return tool
        return {}


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Return cosine similarity in [-1, 1]; returns 0.0 on zero vectors."""

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _rank_by_embedding(tools: list[dict[str, Any]], query_vec: list[float]) -> list[dict[str, Any]]:
    """Rank tools by cosine similarity to query_vec; skip tools without embedding."""

    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tool in tools:
        name = str(tool.get("name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        emb = tool.get("embedding")
        if not isinstance(emb, list) or len(emb) != len(query_vec):
            continue
        sim = _cosine_similarity(query_vec, emb)
        ranked.append({**tool, "score": sim})
    ranked.sort(key=lambda item: (-float(item.get("score", 0)), str(item.get("name", ""))))
    return ranked


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
