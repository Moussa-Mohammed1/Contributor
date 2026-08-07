"""Anthropic Claude provider (Messages API)."""

from __future__ import annotations

import httpx

from keeper.ai.base import AIProvider, AIRequest, AIResponse
from keeper.ai.providers.common import map_http_error

DEFAULT_MODEL = "claude-3-5-haiku-latest"
DEFAULT_BASE = "https://api.anthropic.com/v1"
DEFAULT_VERSION = "2023-06-01"


class AnthropicProvider(AIProvider):
    """Claude via the /messages endpoint."""

    name = "claude"
    requires_key = True

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("model", DEFAULT_MODEL)
        kwargs.setdefault("base_url", DEFAULT_BASE)
        super().__init__(**kwargs)

    def _payload(self, request: AIRequest) -> dict:
        payload: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "system": request.system,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        return payload

    def _extract(self, data: dict) -> str:
        blocks = data.get("content") or []
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    def complete(self, request: AIRequest) -> AIResponse:
        import time

        start = time.perf_counter()
        url = f"{self.base_url.rstrip('/')}/messages"
        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": DEFAULT_VERSION,
            "content-type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=self._payload(request), headers=headers)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # noqa: BLE001
            raise map_http_error(exc, self.name) from exc
        latency = int((time.perf_counter() - start) * 1000)
        return AIResponse(text=self._extract(data), provider=self.name, model=self.model, latency_ms=latency)

    async def complete_async(self, request: AIRequest) -> AIResponse:
        import time

        start = time.perf_counter()
        url = f"{self.base_url.rstrip('/')}/messages"
        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": DEFAULT_VERSION,
            "content-type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=self._payload(request), headers=headers)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # noqa: BLE001
            raise map_http_error(exc, self.name) from exc
        latency = int((time.perf_counter() - start) * 1000)
        return AIResponse(text=self._extract(data), provider=self.name, model=self.model, latency_ms=latency)