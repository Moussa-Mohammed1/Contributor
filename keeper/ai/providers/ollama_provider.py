"""Ollama provider (local LLM runtime)."""

from __future__ import annotations

import httpx

from keeper.ai.base import AIProvider, AIRequest, AIResponse
from keeper.ai.providers.common import map_http_error

DEFAULT_MODEL = "qwen2.5-coder:7b"
DEFAULT_BASE = "http://localhost:11434"


class OllamaProvider(AIProvider):
    """Ollama via the /api/chat endpoint with JSON format enabled."""

    name = "ollama"
    requires_key = False

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("model", DEFAULT_MODEL)
        kwargs.setdefault("base_url", DEFAULT_BASE)
        super().__init__(**kwargs)

    def _payload(self, request: AIRequest) -> dict:
        payload: dict = {
            "model": self.model,
            "stream": False,
            "temperature": self.temperature,
            "num_predict": self.max_tokens,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
        }
        if request.json_mode:
            payload["format"] = "json"
        return payload

    def _extract(self, data: dict) -> str:
        message = data.get("message") or {}
        return message.get("content", "")

    def complete(self, request: AIRequest) -> AIResponse:
        import time

        start = time.perf_counter()
        url = f"{self.base_url.rstrip('/')}/api/chat"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=self._payload(request))
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # noqa: BLE001
            raise map_http_error(exc, self.name) from exc
        latency = int((time.perf_counter() - start) * 1000)
        return AIResponse(text=self._extract(data), provider=self.name, model=self.model, latency_ms=latency)

    async def complete_async(self, request: AIRequest) -> AIResponse:
        import time

        start = time.perf_counter()
        url = f"{self.base_url.rstrip('/')}/api/chat"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=self._payload(request))
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # noqa: BLE001
            raise map_http_error(exc, self.name) from exc
        latency = int((time.perf_counter() - start) * 1000)
        return AIResponse(text=self._extract(data), provider=self.name, model=self.model, latency_ms=latency)