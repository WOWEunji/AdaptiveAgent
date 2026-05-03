"""OpenAI Chat Completions / Responses API client."""

from __future__ import annotations

import os

from adaptive_agent.llms.base import LLMClient


def should_use_openai_responses_api(model: str) -> bool:
    """Return whether the model should use the OpenAI Responses API."""
    return model.lower().startswith("gpt-5")


def validate_openai_api_key(key: str | None) -> str:
    """Validate configured OpenAI API key material."""
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
    # Preserve compatibility with non-standard OpenAI-compatible key formats.
    return k


def format_openai_api_error(*, status_code: int | None, message: str, model: str) -> str | None:
    """Format actionable OpenAI SDK errors for CLI output."""

    if status_code == 401:
        return (
            "OpenAI가 API 키를 거부했습니다(401). "
            ".env의 OPENAI_API_KEY가 유효한 sk- 키인지, 공백·따옴표 오류는 없는지 확인하세요."
        )
    if status_code == 400 and "model" in message.lower():
        return (
            "OpenAI 모델 요청이 실패했습니다(400). "
            f"모델명 `{model}`이 현재 계정/API에서 사용 가능한지 확인하세요. "
            "예: gpt-5.4-nano 또는 gpt-5.4-mini. "
            f"원본: {message}"
        )
    return None


def _extract_openai_error_message(exc: Exception) -> str:
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


class OpenAIClient:
    """Minimal OpenAI client for Chat Completions and Responses APIs."""

    def __init__(self, model: str, *, api_key: str | None = None) -> None:
        self._model = model
        self._api_key = validate_openai_api_key(api_key or os.getenv("OPENAI_API_KEY"))

    def generate(self, prompt: str) -> str:
        from openai import APIError, OpenAI

        client = OpenAI(api_key=self._api_key)
        try:
            if should_use_openai_responses_api(self._model):
                _base: dict = {"model": self._model, "input": prompt}
                for _extra in (
                    {"reasoning": {"effort": "minimal"}, "text": {"verbosity": "low"}, "max_output_tokens": 2048},
                    {"max_output_tokens": 2048},
                    {},
                ):
                    try:
                        response = client.responses.create(**_base, **_extra)
                        return (response.output_text or "").strip()
                    except APIError as e:
                        if getattr(e, "status_code", None) in (401, 403):
                            raise
                        if not _extra:
                            raise
                        continue
            response = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
            choice = response.choices[0].message.content
            return (choice if choice is not None else "").strip()
        except APIError as e:
            message = _extract_openai_error_message(e)
            formatted = format_openai_api_error(
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


def create_openai_client(model: str, *, api_key: str | None = None) -> LLMClient:
    return OpenAIClient(model=model, api_key=api_key)
