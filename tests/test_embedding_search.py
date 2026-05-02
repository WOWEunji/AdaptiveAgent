"""Embedding-based skill search 테스트.

#17 — opt-in 의미 검색. NoopEmbedder 기본 동작은 회귀 0, 진짜 embedder가
주입된 경우만 semantic 분기.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adaptive_agent.agent import AdaptiveAgent
from adaptive_agent.config import AgentConfig
from adaptive_agent.skills import SkillCatalog
from adaptive_agent.skills.embedding import (
    NoopEmbedder,
    cosine_similarity,
    create_embedder,
)


class _DeterministicEmbedder:
    """테스트용 가짜 embedder. text → 고정 벡터 매핑으로 cosine 결과 예측 가능."""

    model_id = "test-deterministic-v1"

    def __init__(self, mapping: dict[str, list[float]]):
        self.mapping = mapping

    def embed(self, text: str) -> list[float] | None:
        if not text.strip():
            return None
        # 정확 매칭 우선, 없으면 키워드 부분 매칭
        if text in self.mapping:
            return list(self.mapping[text])
        for key, vec in self.mapping.items():
            if key in text:
                return list(vec)
        return [0.0, 0.0, 0.0]


def _seed_tool(workspace: Path, *, name: str, code: str) -> dict[str, object]:
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
    create = agent.run_tool("tool_create", {"name": name, "description": "test", "code": code})
    assert create.success, create.error
    validate = agent.run_tool("tool_validate", {"name": name})
    assert validate.success, validate.error
    approve = agent.run_tool("tool_approve", {"name": name})
    assert approve.success, approve.error
    return approve.output


class CosineSimilarityTest(unittest.TestCase):
    def test_identical_vectors_score_one(self) -> None:
        self.assertAlmostEqual(cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]), 1.0, places=5)

    def test_orthogonal_vectors_score_zero(self) -> None:
        self.assertAlmostEqual(cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0, places=5)

    def test_zero_norm_yields_zero(self) -> None:
        self.assertEqual(cosine_similarity([0.0, 0.0], [1.0, 1.0]), 0.0)

    def test_length_mismatch_yields_zero(self) -> None:
        self.assertEqual(cosine_similarity([1.0], [1.0, 1.0]), 0.0)


class EmbedderFactoryTest(unittest.TestCase):
    def test_none_returns_noop(self) -> None:
        embedder = create_embedder("none")
        self.assertIsInstance(embedder, NoopEmbedder)
        self.assertIsNone(embedder.embed("anything"))

    def test_invalid_provider_rejected(self) -> None:
        with self.assertRaises(ValueError):
            create_embedder("xyz")


class CatalogSemanticSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.tool_library = self.workspace / ".adaptive_agent" / "tools"
        self.catalog = SkillCatalog(self.tool_library)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_keyword_mode_unaffected_by_embedder_arg(self) -> None:
        _seed_tool(
            self.workspace,
            name="csv_parser",
            code="def run(arguments):\n    return {'parsed': True}\n",
        )
        results = self.catalog.search("csv", top_k=5, mode="keyword")
        self.assertTrue(any(r["name"] == "csv_parser" for r in results))

    def test_semantic_mode_without_embedding_returns_empty(self) -> None:
        _seed_tool(
            self.workspace,
            name="csv_parser",
            code="def run(arguments):\n    return {}\n",
        )
        embedder = _DeterministicEmbedder({"csv_parser": [1.0, 0.0]})
        # entry는 아직 embedding이 attached되지 않음
        results = self.catalog.search("csv", top_k=5, mode="semantic", embedder=embedder)
        self.assertEqual(results, [])

    def test_semantic_mode_with_attached_embedding_matches(self) -> None:
        _seed_tool(
            self.workspace,
            name="text_summarizer",
            code="def run(arguments):\n    return {'summary': 'x'}\n",
        )
        # 동일 벡터로 attach → query와 cosine = 1
        vec = [1.0, 0.0, 0.0]
        self.catalog.attach_embedding("text_summarizer", vec, embedding_model="test-deterministic-v1")
        embedder = _DeterministicEmbedder({"summary request": vec})

        results = self.catalog.search("summary request", top_k=5, mode="semantic", embedder=embedder, threshold=0.5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "text_summarizer")
        self.assertAlmostEqual(results[0]["score"], 1.0, places=5)

    def test_threshold_filters_low_similarity(self) -> None:
        _seed_tool(
            self.workspace,
            name="edge_case",
            code="def run(arguments):\n    return {}\n",
        )
        self.catalog.attach_embedding("edge_case", [1.0, 0.0, 0.0], embedding_model="test-deterministic-v1")
        embedder = _DeterministicEmbedder({"orthogonal query": [0.0, 1.0, 0.0]})

        # cosine = 0 < threshold 0.4 → 결과 없음
        results = self.catalog.search("orthogonal query", top_k=5, mode="semantic", embedder=embedder, threshold=0.4)
        self.assertEqual(results, [])

    def test_model_mismatch_excluded(self) -> None:
        _seed_tool(
            self.workspace,
            name="legacy",
            code="def run(arguments):\n    return {}\n",
        )
        self.catalog.attach_embedding("legacy", [1.0, 0.0], embedding_model="old-model-v0")

        # 다른 model_id로 embedder 만들면 entry 매칭 안 됨
        embedder = _DeterministicEmbedder({"legacy": [1.0, 0.0]})
        embedder.model_id = "new-model-v1"
        results = self.catalog.search("legacy", top_k=5, mode="semantic", embedder=embedder, threshold=0.0)
        self.assertEqual(results, [])

    def test_auto_mode_with_embedder_uses_semantic(self) -> None:
        _seed_tool(
            self.workspace,
            name="auto_match",
            code="def run(arguments):\n    return {}\n",
        )
        self.catalog.attach_embedding("auto_match", [1.0, 0.0, 0.0], embedding_model="test-deterministic-v1")
        embedder = _DeterministicEmbedder({"some text": [1.0, 0.0, 0.0]})

        results = self.catalog.search("some text", top_k=5, mode="auto", embedder=embedder, threshold=0.5)
        self.assertEqual(len(results), 1)

    def test_auto_mode_without_embedder_falls_back_to_keyword(self) -> None:
        _seed_tool(
            self.workspace,
            name="keyword_only_tool",
            code="def run(arguments):\n    return {}\n",
        )
        results = self.catalog.search("keyword_only", top_k=5, mode="auto", embedder=None)
        self.assertTrue(any(r["name"] == "keyword_only_tool" for r in results))


class ToolApproveAttachesEmbeddingTest(unittest.TestCase):
    def test_approve_with_embedder_attaches_embedding_to_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            tool_library = workspace / ".adaptive_agent" / "tools"

            embedder = _DeterministicEmbedder({})
            # _DeterministicEmbedder는 정확 매칭이 없으면 [0,0,0] 반환 — 테스트 위해 항상 채움
            embedder.embed = lambda text: [0.5, 0.5, 0.5]  # type: ignore[method-assign]

            class _SilentLLM:
                def complete(self, _p):
                    return '{"action":"respond","response":"ok"}'

            agent = AdaptiveAgent(
                config=AgentConfig(
                    workspace_dir=workspace,
                    tool_library_dir=tool_library,
                    session_dir=workspace / ".adaptive_agent" / "sessions",
                    embedding_provider="none",  # AdaptiveAgent 인스턴스 자체는 noop
                ),
                llm_client=_SilentLLM(),
            )
            # tool_approve handler에 _embedder 직접 주입해서 호출
            agent.run_tool("tool_create", {"name": "embed_test", "description": "x", "code": "def run(a): return {}"})
            agent.run_tool("tool_validate", {"name": "embed_test"})
            approve = agent.run_tool("tool_approve", {"name": "embed_test", "_embedder": embedder})

            self.assertTrue(approve.success)
            catalog = SkillCatalog(tool_library)
            entry = next(t for t in catalog.list() if t["name"] == "embed_test")
            self.assertEqual(entry["embedding"], [0.5, 0.5, 0.5])
            self.assertEqual(entry["embedding_model"], embedder.model_id)


if __name__ == "__main__":
    unittest.main()
