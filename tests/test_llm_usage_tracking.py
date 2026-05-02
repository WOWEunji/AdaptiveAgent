"""LLM cost/token usage 추적 단위 테스트.

각 provider client가 ``last_usage``를 채우면 agent.py가 매 호출마다 읽어
``state.llm_usage_records``에 누적하고, AgentResponse.llm_usage_summary로
요약해 노출한다는 계약을 잠근다. usage가 없는 stub LLM도 회귀 0.
"""

from __future__ import annotations

import unittest

from adaptive_agent.agent import AdaptiveAgent
from adaptive_agent.config import AgentConfig
from adaptive_agent.llms.usage import LLMUsage, aggregate_usage


class _PricedStubLLM:
    """매 호출마다 last_usage를 채우는 stub."""

    def __init__(self, response: str, *, model: str = "gpt-4o-mini", in_tokens: int = 100, out_tokens: int = 50):
        self.response = response
        self._model = model
        self._in = in_tokens
        self._out = out_tokens
        self.last_usage: LLMUsage | None = None
        self.calls = 0

    def complete(self, _prompt: str) -> str:
        self.calls += 1
        self.last_usage = LLMUsage.from_counts(
            provider="openai",
            model=self._model,
            input_tokens=self._in,
            output_tokens=self._out,
        )
        return self.response

    def generate(self, prompt: str) -> str:
        return self.complete(prompt)


class _UsageOptionalStubLLM:
    """last_usage를 채우지 않는 stub (회귀 케이스)."""

    last_usage: LLMUsage | None = None

    def complete(self, _prompt: str) -> str:
        return '{"action":"respond","response":"ok"}'

    def generate(self, prompt: str) -> str:
        return self.complete(prompt)


class LLMUsageDataclassTest(unittest.TestCase):
    def test_from_counts_computes_total_and_known_cost(self) -> None:
        usage = LLMUsage.from_counts(
            provider="openai", model="gpt-4o-mini", input_tokens=1_000_000, output_tokens=500_000
        )
        self.assertEqual(usage.total_tokens, 1_500_000)
        # 1M*0.15 + 0.5M*0.60 = 0.15 + 0.30 = 0.45
        self.assertEqual(usage.estimated_cost_usd, 0.45)

    def test_unknown_model_has_no_cost(self) -> None:
        usage = LLMUsage.from_counts(
            provider="ollama", model="qwen3.5:2b", input_tokens=100, output_tokens=50
        )
        self.assertIsNone(usage.estimated_cost_usd)

    def test_aggregate_empty(self) -> None:
        summary = aggregate_usage([])
        self.assertEqual(summary["calls"], 0)
        self.assertEqual(summary["total_tokens"], 0)
        self.assertEqual(summary["estimated_cost_usd"], 0.0)
        self.assertEqual(summary["by_model"], {})

    def test_aggregate_groups_by_model(self) -> None:
        records = [
            LLMUsage.from_counts(provider="openai", model="gpt-4o-mini", input_tokens=200, output_tokens=100),
            LLMUsage.from_counts(provider="openai", model="gpt-4o-mini", input_tokens=300, output_tokens=150),
            LLMUsage.from_counts(provider="ollama", model="qwen3.5:2b", input_tokens=500, output_tokens=200),
        ]
        summary = aggregate_usage(records)
        self.assertEqual(summary["calls"], 3)
        self.assertEqual(summary["input_tokens"], 1000)
        self.assertEqual(summary["output_tokens"], 450)
        self.assertEqual(summary["by_model"]["gpt-4o-mini"]["calls"], 2)
        self.assertEqual(summary["by_model"]["qwen3.5:2b"]["calls"], 1)


class AgentUsageRecordingTest(unittest.TestCase):
    def test_priced_stub_accumulates_usage_in_summary(self) -> None:
        llm = _PricedStubLLM(
            '{"action":"respond","response":"hi"}',
            model="gpt-4o-mini",
            in_tokens=200,
            out_tokens=100,
        )
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)
        result = agent.run("usage test")

        self.assertEqual(llm.calls, 1, "정확히 1회 호출")
        self.assertIsNotNone(result.llm_usage_summary)
        self.assertEqual(result.llm_usage_summary["calls"], 1)
        self.assertEqual(result.llm_usage_summary["input_tokens"], 200)
        self.assertEqual(result.llm_usage_summary["output_tokens"], 100)
        self.assertEqual(result.llm_usage_summary["total_tokens"], 300)
        # 200/1M * 0.15 + 100/1M * 0.60 = 0.00003 + 0.00006 = 0.00009
        self.assertAlmostEqual(result.llm_usage_summary["estimated_cost_usd"], 9e-5, places=8)

    def test_usage_records_event_per_call(self) -> None:
        llm = _PricedStubLLM('{"action":"respond","response":"x"}')
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)
        result = agent.run("event test")

        usage_events = [e for e in result.events if e.name == "llm_call_recorded"]
        self.assertEqual(len(usage_events), 1)
        self.assertEqual(usage_events[0].details["purpose"], "plan")
        self.assertEqual(usage_events[0].details["provider"], "openai")

    def test_stub_without_usage_yields_no_summary(self) -> None:
        llm = _UsageOptionalStubLLM()
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=llm)
        result = agent.run("no usage stub")

        self.assertIsNone(result.llm_usage_summary, "usage 미지원 stub은 summary가 None")
        usage_events = [e for e in result.events if e.name == "llm_call_recorded"]
        self.assertEqual(usage_events, [])


if __name__ == "__main__":
    unittest.main()
