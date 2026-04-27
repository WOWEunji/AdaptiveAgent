"""LLM provider interfaces."""

from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    """LLM 구현체가 따라야 하는 최소 인터페이스."""

    def generate(self, prompt: str) -> str:
        """프롬프트를 전송하고 텍스트 응답을 반환합니다."""
