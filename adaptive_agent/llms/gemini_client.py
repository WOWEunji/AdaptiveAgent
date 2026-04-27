"""Google Gemini (Generative Language API) client."""

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
    """`google-generativeai` 기반 최소 클라이언트."""

    def __init__(self, model: str, *, api_key: str | None = None) -> None:
        self._model = model
        self._api_key = validate_gemini_api_key(_resolve_api_key(api_key))

    def generate(self, prompt: str) -> str:
        import google.generativeai as genai

        genai.configure(api_key=self._api_key)
        gm = genai.GenerativeModel(self._model)
        response = gm.generate_content(prompt)
        text = getattr(response, "text", None)
        if text:
            return text
        if response.candidates:
            parts = response.candidates[0].content.parts
            return "".join(getattr(p, "text", "") for p in parts)
        return ""


def create_gemini_client(model: str, *, api_key: str | None = None) -> LLMClient:
    return GeminiClient(model=model, api_key=api_key)
