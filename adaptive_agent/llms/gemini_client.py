"""Google Gemini client using the Google GenAI SDK."""

from __future__ import annotations

import os

from adaptive_agent.llms.base import LLMClient


def _resolve_api_key(explicit: str | None) -> str:
    return (explicit or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()


def validate_gemini_api_key(key: str | None) -> str:
    if not key or not (k := key.strip()):
        msg = "GEMINI_API_KEY 또는 GOOGLE_API_KEY가 설정되어 있지 않습니다."
        raise ValueError(msg)
    lower = k.lower()
    if any(
        p in lower
        for p in (
            "your_gemini",
            "your_api_key",
            "changeme",
            "placeholder",
            "paste_here",
        )
    ):
        msg = (
            "GEMINI_API_KEY가 예시·플레이스홀더처럼 보입니다. "
            "Google AI Studio 등에서 발급한 실제 키를 .env에 넣어주세요."
        )
        raise ValueError(msg)
    return k


class GeminiClient:
    """Gemini Developer API client for API-key based access."""

    def __init__(self, model: str, *, api_key: str | None = None) -> None:
        self._model = model
        self._api_key = validate_gemini_api_key(_resolve_api_key(api_key))

    def complete(self, prompt: str) -> str:
        """Compatibility completion method used by the agent core."""

        return self.generate(prompt)

    def generate(self, prompt: str) -> str:
        from google import genai

        client = genai.Client(api_key=self._api_key)
        try:
            response = client.models.generate_content(
                model=self._model,
                contents=prompt,
            )
        except Exception as e:
            msg = (
                "Gemini API 호출에 실패했습니다. "
                "키는 AI Studio 발급인지, 모델 ID·과금·할당량을 확인하세요. "
                f"원본: {e}"
            )
            raise ValueError(msg) from e

        text = getattr(response, "text", None)
        if text:
            return text.strip()
        msg = (
            "Gemini가 빈 텍스트를 반환했습니다. "
            "안전 필터 차단·모델 미지원(모델명 오타)·429 등일 수 있습니다. "
            "`GEMINI_MODEL`을 gemini-2.5-flash-lite 등으로 바꿔 보세요."
        )
        raise ValueError(msg)


def create_gemini_client(model: str, *, api_key: str | None = None) -> LLMClient:
    return GeminiClient(model=model, api_key=api_key)
