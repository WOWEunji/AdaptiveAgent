"""Google Gemini (Generative Language API) client."""

from __future__ import annotations

import os

from adaptive_agent.llms.base import LLMClient


def _resolve_api_key(explicit: str | None) -> str:
    return (explicit or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()


class GeminiClient:
    """`google-generativeai` 기반 최소 클라이언트."""

    def __init__(self, model: str, *, api_key: str | None = None) -> None:
        self._model = model
        self._api_key = _resolve_api_key(api_key)
        if not self._api_key:
            msg = "GEMINI_API_KEY 또는 GOOGLE_API_KEY가 설정되어 있지 않습니다."
            raise ValueError(msg)

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
