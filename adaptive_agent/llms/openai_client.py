"""OpenAI Chat Completions / Responses API client."""

from __future__ import annotations

import os

from adaptive_agent.llms.base import LLMClient


def should_use_openai_responses_api(model: str) -> bool:
    """gpt-5 계열 등은 Responses API 사용이 기본인 경우가 많다."""
    return model.lower().startswith("gpt-5")


def validate_openai_api_key(key: str | None) -> str:
    """로컬 스모크 테스트용: 비어 있거나 예시 문구면 즉시 실패."""
    if not key or not (k := key.strip()):
        msg = "OPENAI_API_KEY가 설정되어 있지 않습니다."
        raise ValueError(msg)
    lower = k.lower()
    if any(
        p in lower
        for p in (
            "your_openai",
            "your_ope",
            "your-api-key",
            "changeme",
            "placeholder",
            "paste_here",
            "sk-test",
        )
    ):
        msg = (
            "OPENAI_API_KEY가 예시·플레이스홀더처럼 보입니다. "
            ".env에 platform.openai.com 에서 발급한 sk-… 실제 키를 넣어주세요."
        )
        raise ValueError(msg)
    if k.startswith("sk-"):
        return k
    # 비표준 포맷은 경고 없이 통과(호환 키 형식 대비)
    return k


class OpenAIClient:
    """OpenAI `chat.completions` 또는 `responses` 기반 최소 클라이언트."""

    def __init__(self, model: str, *, api_key: str | None = None) -> None:
        self._model = model
        self._api_key = validate_openai_api_key(api_key or os.getenv("OPENAI_API_KEY"))

    def generate(self, prompt: str) -> str:
        from openai import APIError, OpenAI

        client = OpenAI(api_key=self._api_key)
        try:
            if should_use_openai_responses_api(self._model):
                try:
                    response = client.responses.create(
                        model=self._model,
                        input=prompt,
                        reasoning={"effort": "minimal"},
                        text={"verbosity": "low"},
                        max_output_tokens=2048,
                    )
                except APIError as e:
                    if getattr(e, "status_code", None) == 401:
                        raise
                    response = client.responses.create(
                        model=self._model,
                        input=prompt,
                        max_output_tokens=2048,
                    )
                return (response.output_text or "").strip()
            response = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
            )
            choice = response.choices[0].message.content
            return (choice if choice is not None else "").strip()
        except APIError as e:
            if getattr(e, "status_code", None) == 401:
                msg = (
                    "OpenAI가 API 키를 거부했습니다(401). "
                    ".env의 OPENAI_API_KEY가 유효한 sk- 키인지, 공백·따옴표 오류는 없는지 확인하세요."
                )
                raise ValueError(msg) from e
            raise


def create_openai_client(model: str, *, api_key: str | None = None) -> LLMClient:
    return OpenAIClient(model=model, api_key=api_key)
