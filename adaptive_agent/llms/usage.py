"""LLM usage tracking primitives.

각 provider client는 ``complete()`` 후 ``self.last_usage``에 한 번의 호출
사용량을 채운다 (없으면 ``None``). Agent 쪽이 매 호출 후 이 값을 읽어
세션 누적에 합산한다.

모든 필드는 정수 토큰 (input/output/total). 비용은 옵션이며 알려진
모델만 추정 (지나치게 정확한 가격 추적은 별도 issue).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# USD per 1M tokens. 추정값(public pricing 기준 2025년경, 정밀 추적은 의도적
# 비범위). 모델명이 키에 없으면 비용 None으로 둔다.
_KNOWN_MODEL_PRICING_USD_PER_M_TOKENS: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-5-nano": {"input": 0.20, "output": 0.80},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
}


@dataclass(frozen=True)
class LLMUsage:
    """Single LLM call usage record."""

    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float | None = None

    @classmethod
    def from_counts(
        cls,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> "LLMUsage":
        total = input_tokens + output_tokens
        cost = _estimate_cost_usd(model, input_tokens, output_tokens)
        return cls(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total,
            estimated_cost_usd=cost,
        )


def _estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    pricing = _KNOWN_MODEL_PRICING_USD_PER_M_TOKENS.get(model)
    if not pricing:
        return None
    return round(
        (input_tokens / 1_000_000) * pricing["input"]
        + (output_tokens / 1_000_000) * pricing["output"],
        6,
    )


def aggregate_usage(records: Iterable[LLMUsage]) -> dict[str, object]:
    """Aggregate a sequence of LLMUsage records into a summary dict.

    The agent attaches the per-session total to ``AgentResponse``-level
    diagnostics via ``state.events`` so a CLI/JSON consumer can compute
    spend without re-parsing every event.
    """

    records = list(records)
    if not records:
        return {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
            "by_model": {},
        }

    total_in = sum(r.input_tokens for r in records)
    total_out = sum(r.output_tokens for r in records)
    total_cost = round(sum((r.estimated_cost_usd or 0.0) for r in records), 6)

    by_model: dict[str, dict[str, object]] = {}
    for record in records:
        bucket = by_model.setdefault(
            record.model,
            {"calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0},
        )
        bucket["calls"] = int(bucket["calls"]) + 1
        bucket["input_tokens"] = int(bucket["input_tokens"]) + record.input_tokens
        bucket["output_tokens"] = int(bucket["output_tokens"]) + record.output_tokens
        bucket["estimated_cost_usd"] = round(
            float(bucket["estimated_cost_usd"]) + (record.estimated_cost_usd or 0.0), 6
        )
    return {
        "calls": len(records),
        "input_tokens": total_in,
        "output_tokens": total_out,
        "total_tokens": total_in + total_out,
        "estimated_cost_usd": total_cost,
        "by_model": by_model,
    }
