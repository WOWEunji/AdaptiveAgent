"""Ollama LLM client — calls the Ollama HTTP REST API directly."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from adaptive_agent.llms.base import LLMClient

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 11434


def _base_url(host: str, port: int) -> str:
    host = host.rstrip("/")
    if host.startswith(("http://", "https://")):
        return f"{host}:{port}"
    return f"http://{host}:{port}"


class OllamaClient:
    """LLM client backed by a local Ollama server via its HTTP REST API."""

    def __init__(
        self,
        model: str,
        *,
        host: str | None = None,
        port: int | None = None,
        timeout_seconds: float = 60.0,
        num_predict: int = 256,
        think: bool = False,
    ) -> None:
        self.model = model
        self._base = _base_url(host or _DEFAULT_HOST, port or _DEFAULT_PORT)
        self.timeout_seconds = timeout_seconds
        self.num_predict = num_predict
        self.think = think

    def generate(self, prompt: str) -> str:
        url = f"{self._base}/api/chat"
        payload: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "think": self.think,
            "options": {"temperature": 0, "num_predict": self.num_predict},
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                body = json.loads(resp.read())
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Ollama 서버에 연결할 수 없습니다 ({self._base}). "
                f"ollama serve가 실행 중인지 확인하세요. 원본: {exc}"
            ) from exc
        return str(body["message"]["content"])

    def complete(self, prompt: str) -> str:
        """Compatibility completion method used by the agent core."""

        return self.generate(prompt)


def create_ollama_client(
    model: str,
    *,
    host: str | None = None,
    port: int | None = None,
    timeout_seconds: float = 60.0,
    num_predict: int = 256,
    think: bool = False,
) -> LLMClient:
    return OllamaClient(
        model=model,
        host=host,
        port=port,
        timeout_seconds=timeout_seconds,
        num_predict=num_predict,
        think=think,
    )
