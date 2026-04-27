"""OpenAI 라우팅·키 검증 단위 테스트."""

from __future__ import annotations

import pytest

from adaptive_agent.llms.openai_client import (
    should_use_openai_responses_api,
    validate_openai_api_key,
)


def test_should_use_openai_responses_api_gpt5_family() -> None:
    assert should_use_openai_responses_api("gpt-5-nano") is True
    assert should_use_openai_responses_api("gpt-5-nano-2025-08-07") is True
    assert should_use_openai_responses_api("gpt-4o-mini") is False
    assert should_use_openai_responses_api("gpt-4.1-nano") is False


def test_validate_openai_api_key_rejects_placeholder() -> None:
    with pytest.raises(ValueError, match="예시"):
        validate_openai_api_key("your_openai_key_here")


def test_validate_openai_api_key_accepts_sk_prefix() -> None:
    key = "sk-" + "a" * 45
    assert validate_openai_api_key(key) == key
