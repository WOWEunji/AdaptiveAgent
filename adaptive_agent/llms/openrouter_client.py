"""OpenRouter client — OpenAI-compatible API via openrouter.ai."""

from __future__ import annotations

import os

from adaptive_agent.llms.base import LLMClient

# OpenRouter에서 권장하는 식별 헤더 값.
_HTTP_REFERER = "https://github.com/adaptive-agent"

_PLACEHOLDER_PATTERNS = (
    "your_openrouter",
    "your-api-key",
    "changeme",
    "placeholder",
    "paste_here",
)


def validate_openrouter_api_key(key: str | None) -> str:
    """Validate configured OpenRouter API key material.

    Raises ValueError for missing or placeholder keys so the caller can surface
    an actionable message before any network request is made.
    """
    if not key or not (k := key.strip()):
        msg = "OPENROUTER_API_KEY가 설정되어 있지 않습니다."
        raise ValueError(msg)
    lower = k.lower()
    if any(p in lower for p in _PLACEHOLDER_PATTERNS):
        msg = (
            "OPENROUTER_API_KEY가 예시·플레이스홀더처럼 보입니다. "
            ".env에 openrouter.ai에서 발급한 실제 키를 넣어주세요."
        )
        raise ValueError(msg)
    return k


def format_openrouter_api_error(*, status_code: int | None, message: str, model: str) -> str | None:
    """Format actionable OpenRouter SDK errors for CLI output."""
    if status_code == 401:
        return (
            "OpenRouter가 API 키를 거부했습니다(401). "
            ".env의 OPENROUTER_API_KEY가 유효한 키인지 확인하세요."
        )
    if status_code == 400 and "model" in message.lower():
        return (
            "OpenRouter 모델 요청이 실패했습니다(400). "
            f"모델명 `{model}`이 OpenRouter에서 사용 가능한지 확인하세요. "
            f"원본: {message}"
        )
    return None


class OpenRouterClient:
    """OpenAI SDK를 이용해 OpenRouter API에 연결하는 클라이언트."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._model = model
        self._api_key = validate_openrouter_api_key(api_key or os.getenv("OPENROUTER_API_KEY"))
        self._timeout = timeout

    def generate(self, prompt: str) -> str:
        """Generate text from a prompt via OpenRouter Chat Completions."""
        from openai import APIError, OpenAI

        client = OpenAI(
            api_key=self._api_key,
            base_url="https://openrouter.ai/api/v1",
            timeout=self._timeout,
            default_headers={"HTTP-Referer": _HTTP_REFERER},
        )
        try:
            response = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
            if not response.choices:
                raise ValueError(
                    "OpenRouter가 빈 응답을 반환했습니다. "
                    "모델 할당량이 초과되었거나 요청이 차단되었을 수 있습니다."
                )
            choice = response.choices[0].message.content
            return (choice if choice is not None else "").strip()
        except APIError as e:
            message = _extract_error_message(e)
            formatted = format_openrouter_api_error(
                status_code=getattr(e, "status_code", None),
                message=message,
                model=self._model,
            )
            if formatted:
                raise ValueError(formatted) from e
            raise

    def complete(self, prompt: str) -> str:
        """Compatibility completion method used by the agent core."""
        return self.generate(prompt)


def _extract_error_message(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            payload = response.json()
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or "")
    return str(exc)


def create_openrouter_client(model: str, *, api_key: str | None = None) -> LLMClient:
    """Factory function returning an OpenRouterClient."""
    return OpenRouterClient(model=model, api_key=api_key)
