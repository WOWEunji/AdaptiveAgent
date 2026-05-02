"""Multi-perspective parallel execution 테스트.

#20 — researcher / pm / architect / reviewer / qa 페르소나를 동시에
호출. implementer는 거부.
"""

from __future__ import annotations

import threading
import time
import unittest

from adaptive_agent.agent import AdaptiveAgent
from adaptive_agent.config import AgentConfig
from adaptive_agent.perspectives import (
    PERSPECTIVE_PROMPTS,
    _PerspectiveLLM,
    resolve_perspective_keys,
)


class _RecordingStubLLM:
    """매 complete 호출의 prompt를 그대로 기록하는 LLM stub."""

    last_usage = None

    def __init__(self, response: str = '{"action":"respond","response":"ok"}'):
        self.response = response
        self.prompts: list[str] = []
        self._lock = threading.Lock()

    def complete(self, prompt: str) -> str:
        with self._lock:
            self.prompts.append(prompt)
        return self.response

    def generate(self, prompt: str) -> str:
        return self.complete(prompt)


class _SlowLLM(_RecordingStubLLM):
    """delay seconds slept inside each call to verify parallelism."""

    def __init__(self, delay: float, response: str = '{"action":"respond","response":"slow"}'):
        super().__init__(response)
        self.delay = delay

    def complete(self, prompt: str) -> str:
        time.sleep(self.delay)
        return super().complete(prompt)


class ResolvePerspectiveKeysTest(unittest.TestCase):
    def test_short_aliases(self) -> None:
        self.assertEqual(
            resolve_perspective_keys("r,p,a,c,q"),
            ["researcher", "pm", "architect", "reviewer", "qa"],
        )

    def test_full_names(self) -> None:
        self.assertEqual(
            resolve_perspective_keys(["researcher", "qa"]),
            ["researcher", "qa"],
        )

    def test_dedup_preserves_order(self) -> None:
        self.assertEqual(resolve_perspective_keys("r,p,r,a,p"), ["researcher", "pm", "architect"])

    def test_empty_input_rejected(self) -> None:
        with self.assertRaises(ValueError):
            resolve_perspective_keys("")
        with self.assertRaises(ValueError):
            resolve_perspective_keys([])

    def test_unknown_key_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            resolve_perspective_keys("xyz")
        self.assertIn("xyz", str(ctx.exception))

    def test_implementer_rejected(self) -> None:
        for key in ("implementer", "i", "IMPLEMENTER"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError) as ctx:
                    resolve_perspective_keys(key)
                self.assertIn("implementer", str(ctx.exception))


class PerspectiveLLMWrapperTest(unittest.TestCase):
    def test_wraps_prompt_with_system_prefix(self) -> None:
        base = _RecordingStubLLM()
        wrapped = _PerspectiveLLM(base, system_prefix="You are PM.")

        wrapped.complete("user task here")

        self.assertEqual(len(base.prompts), 1)
        self.assertIn("You are PM.", base.prompts[0])
        self.assertIn("user task here", base.prompts[0])

    def test_propagates_last_usage_from_base(self) -> None:
        # base가 last_usage를 채우는 경우(예: cost-tracking 브랜치 머지 후),
        # wrapper는 그 값을 그대로 노출해야 한다. 여기서는 일반 객체로 시뮬.
        class _UsagePayload:
            input_tokens = 10
            output_tokens = 5

        base = _RecordingStubLLM()
        base.last_usage = _UsagePayload()
        wrapped = _PerspectiveLLM(base, system_prefix="X")
        wrapped.complete("hi")
        self.assertIs(wrapped.last_usage, base.last_usage)


class RunPerspectivesTest(unittest.TestCase):
    def test_returns_one_response_per_perspective(self) -> None:
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=_RecordingStubLLM())
        results = agent.run_perspectives("아키텍처 검토 부탁", "r,p,a")

        self.assertEqual(set(results.keys()), {"researcher", "pm", "architect"})
        for key, resp in results.items():
            with self.subTest(perspective=key):
                self.assertEqual(resp.task, "아키텍처 검토 부탁")

    def test_each_perspective_uses_different_prompt_prefix(self) -> None:
        # 각 perspective가 독립된 wrapped LLM을 받으므로 base에 누적되는
        # 프롬프트의 접두 system 메시지가 perspective마다 다르다.
        recorded_per_perspective: dict[str, list[str]] = {}
        lock = threading.Lock()

        class _CapturingLLM:
            last_usage = None

            def complete(self, prompt: str) -> str:
                # 어느 perspective의 시스템 프롬프트가 들어있는지 기록
                with lock:
                    for key, system_text in PERSPECTIVE_PROMPTS.items():
                        marker = system_text.split(".")[0][:30]  # 첫 문장 일부
                        if marker in prompt:
                            recorded_per_perspective.setdefault(key, []).append(prompt)
                            break
                return '{"action":"respond","response":"acknowledged"}'

            def generate(self, prompt: str) -> str:
                return self.complete(prompt)

        agent = AdaptiveAgent(config=AgentConfig(), llm_client=_CapturingLLM())
        agent.run_perspectives("task", ["researcher", "pm", "architect"])

        self.assertIn("researcher", recorded_per_perspective)
        self.assertIn("pm", recorded_per_perspective)
        self.assertIn("architect", recorded_per_perspective)

    def test_implementer_rejected_via_run_perspectives(self) -> None:
        agent = AdaptiveAgent(config=AgentConfig(), llm_client=_RecordingStubLLM())
        with self.assertRaises(ValueError):
            agent.run_perspectives("x", "implementer")
        with self.assertRaises(ValueError):
            agent.run_perspectives("x", "r,implementer")

    def test_parallel_execution_is_actually_parallel(self) -> None:
        # 3 perspectives * 0.2s sleep — sequential would take ~0.6s, parallel < 0.4s
        delay = 0.2
        agent = AdaptiveAgent(
            config=AgentConfig(max_parallel_perspectives=3),
            llm_client=_SlowLLM(delay=delay),
        )

        started = time.monotonic()
        results = agent.run_perspectives("측정", "r,p,a")
        elapsed = time.monotonic() - started

        self.assertEqual(len(results), 3)
        self.assertLess(elapsed, delay * 2.5, f"병렬 실행이 너무 느림 ({elapsed:.2f}s)")

    def test_one_perspective_failure_does_not_kill_others(self) -> None:
        class _PartialFailLLM:
            last_usage = None

            def __init__(self):
                self.calls = 0
                self._lock = threading.Lock()

            def complete(self, prompt: str) -> str:
                with self._lock:
                    self.calls += 1
                # researcher의 prompt prefix를 받으면 실패시킴
                if "RESEARCHER perspective" in prompt:
                    raise RuntimeError("researcher boom")
                return '{"action":"respond","response":"ok"}'

            def generate(self, prompt: str) -> str:
                return self.complete(prompt)

        agent = AdaptiveAgent(config=AgentConfig(), llm_client=_PartialFailLLM())
        results = agent.run_perspectives("실패 격리", "r,p,a")

        # 실패 격리: 모든 perspective가 결과를 반환해야 함 (실패도 격리된 결과로)
        self.assertEqual(set(results.keys()), {"researcher", "pm", "architect"})
        # researcher는 LLM 예외로 실패 — router가 이를 llm_error로 분류하거나
        # 우리 perspective wrapper가 perspective_error로 잡거나 둘 다 OK
        self.assertIn(results["researcher"].action, {"llm_error", "perspective_error"})
        # 나머지는 정상 응답 (action="llm")
        self.assertEqual(results["pm"].action, "llm")
        self.assertEqual(results["architect"].action, "llm")


if __name__ == "__main__":
    unittest.main()
