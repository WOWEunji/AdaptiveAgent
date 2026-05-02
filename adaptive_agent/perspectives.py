"""Multi-perspective parallel execution (issue #20).

다중 페르소나(연구·PM·아키텍트·리뷰어·QA)가 같은 task를 독립적으로 분석해
서로 다른 관점의 응답을 한번에 받는 기능. 라우터 자체는 sequential 유지하고,
"여러 관점 분석"만 옵트인으로 병렬화한다.

설계 결정 (issue #20):
- implementer는 병렬 호출 금지 (write 충돌). 명시적으로 거부.
- 페르소나는 LLM에 prepended system prompt로만 구분. 별도 agents/ 클래스
  추가 안 함 — 페르소나는 본질적으로 다른 관점(prompt prefix)이지 다른
  실행 흐름이 아니다.
- ThreadPoolExecutor로 동시 호출. AgentConfig.max_parallel_perspectives
  (default 3)로 제한.
- 한 페르소나 실패해도 나머지는 결과 반환 (best-effort).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Callable

from adaptive_agent.llms.base import LLMClient

if TYPE_CHECKING:
    from adaptive_agent.agent import AdaptiveAgent, AgentResponse


# 페르소나 키 → 시스템 프롬프트. 짧은 영어로 작성해 토큰 절약.
PERSPECTIVE_PROMPTS: dict[str, str] = {
    "researcher": (
        "You are the RESEARCHER perspective. Analyze the user task in light of "
        "academic references, prior work, and known limitations. Discuss "
        "feature value vs. risks before any implementation suggestion. Do not "
        "produce code. Focus on what's worth building and what is overreach."
    ),
    "pm": (
        "You are the PM perspective. Translate the user task into a planning "
        "document with: (1) idea clarification with goal and expected impact, "
        "(2) development plan with files/paths and data flow, (3) test plan "
        "with acceptance criteria as a checklist. Do not produce code."
    ),
    "architect": (
        "You are the ARCHITECT perspective. Define module boundaries, file "
        "list (modify vs. create), data and control flow, and one-line risks "
        "with alternatives. Do not produce code. Do not introduce framework "
        "dependencies (no LangChain, no Claude Agent SDK)."
    ),
    "reviewer": (
        "You are the CODE REVIEWER perspective. Evaluate the user task or the "
        "diff at hand using exactly these sections: 1) expected risks, 2) "
        "limitations, 3) what was done well, 4) what to keep, 5) improvements "
        "ranked Must fix / Should fix / Nice to have. Each call out a file or "
        "function. Do not rewrite code."
    ),
    "qa": (
        "You are the QA perspective. Turn the user task or change set into a "
        "concrete acceptance-criteria checklist. Record commands you would "
        "run, expected output, and pass/fail/partial. Do not change code."
    ),
}

# Short aliases used by CLI: --perspectives r,p,a,c,q
PERSPECTIVE_ALIASES: dict[str, str] = {
    "r": "researcher",
    "p": "pm",
    "a": "architect",
    "c": "reviewer",
    "code_reviewer": "reviewer",
    "q": "qa",
    "researcher": "researcher",
    "pm": "pm",
    "architect": "architect",
    "reviewer": "reviewer",
    "qa": "qa",
}

PERSPECTIVES_FORBIDDEN_FOR_PARALLEL = frozenset({"implementer", "i"})


def resolve_perspective_keys(raw: list[str] | str) -> list[str]:
    """Normalize CLI input (e.g. 'r,p,a' or ['researcher','pm']) to canonical keys."""

    if isinstance(raw, str):
        tokens = [t.strip() for t in raw.split(",") if t.strip()]
    else:
        tokens = [str(t).strip() for t in raw if str(t).strip()]

    resolved: list[str] = []
    for token in tokens:
        lowered = token.lower()
        if lowered in PERSPECTIVES_FORBIDDEN_FOR_PARALLEL:
            raise ValueError(
                "implementer 페르소나는 병렬 호출을 지원하지 않습니다 "
                "(write 충돌 위험). 단독 사용하세요."
            )
        if lowered not in PERSPECTIVE_ALIASES:
            raise ValueError(
                f"알 수 없는 페르소나: {token!r}. 사용 가능: "
                + ", ".join(sorted(set(PERSPECTIVE_ALIASES.values())))
            )
        canonical = PERSPECTIVE_ALIASES[lowered]
        if canonical not in resolved:  # dedup, preserve order
            resolved.append(canonical)
    if not resolved:
        raise ValueError("최소 한 개 이상의 페르소나를 지정해야 합니다.")
    return resolved


class _PerspectiveLLM:
    """LLMClient wrapper that prepends a perspective system prompt.

    Thread-safe: each ``complete`` acquires a per-instance lock around the
    base call so ``last_usage`` reads correspond to the same call. Real
    provider clients (OpenAI/Gemini/Ollama) create fresh API clients per
    call inside ``generate``, so this lock is sufficient for usage tracking
    correctness without serializing across perspectives.

    ``last_usage`` is forwarded from the base client when available
    (cost-tracking branch). When the base does not expose usage, this stays
    ``None`` and the agent simply skips usage recording for the wrapper.
    """

    def __init__(self, base: LLMClient, system_prefix: str) -> None:
        self.base = base
        self.system_prefix = system_prefix
        self.last_usage: object | None = None
        self._lock = threading.Lock()

    def _wrap(self, prompt: str) -> str:
        return f"{self.system_prefix}\n\n----\n\n{prompt}"

    def generate(self, prompt: str) -> str:
        return self.complete(prompt)

    def complete(self, prompt: str) -> str:
        with self._lock:
            result = self.base.complete(self._wrap(prompt))
            self.last_usage = getattr(self.base, "last_usage", None)
        return result


def run_perspectives_parallel(
    *,
    agent: "AdaptiveAgent",
    task: str,
    perspectives: list[str],
    max_workers: int,
    build_per_perspective_agent: Callable[[str, str], "AdaptiveAgent"],
) -> dict[str, "AgentResponse"]:
    """Execute ``task`` under each perspective in parallel; return per-key result.

    ``build_per_perspective_agent(perspective_key, system_prefix)`` returns an
    ``AdaptiveAgent`` instance whose LLM client is wrapped with the
    perspective's system prefix. The factory is the agent's responsibility
    so this module stays decoupled from the orchestration class.

    Failures are isolated: a single perspective raising still returns
    results for the others. The exception is captured as an ``AgentResponse``
    with ``action='perspective_error'``.
    """

    from adaptive_agent.agent import AgentResponse  # avoid circular import

    results: dict[str, AgentResponse] = {}
    workers = max(1, min(max_workers, len(perspectives)))

    def _run_one(key: str) -> tuple[str, "AgentResponse"]:
        prefix = PERSPECTIVE_PROMPTS[key]
        sub_agent = build_per_perspective_agent(key, prefix)
        try:
            return key, sub_agent.run(task)
        except Exception as exc:
            return key, AgentResponse(
                task=task,
                output=f"perspective {key} 실행 실패: {exc}",
                action="perspective_error",
            )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_run_one, key) for key in perspectives]
        for future in as_completed(futures):
            key, response = future.result()
            results[key] = response
    return results
