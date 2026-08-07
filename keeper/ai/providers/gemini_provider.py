"""Gemini provider (Google Generative Language API)."""

from __future__ import annotations

import httpx

from keeper.ai.base import AIProvider, AIRequest, AIResponse
from keeper.ai.providers.common import map_http_error

DEFAULT_MODEL = "gemini-2.0-flash"
DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(AIProvider):
    """Google Gemini via the generateContent endpoint."""

    name = "gemini"
    requires_key = True

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("model", DEFAULT_MODEL)
        kwargs.setdefault("base_url", DEFAULT_BASE)
        super().__init__(**kwargs)

    def _payload(self, request: AIRequest) -> dict:
        parts: list[dict] = [
            {"text": request.system},
            {"text": request.prompt},
        ]
        payload: dict = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
                "responseMimeType": "application/json" if request.json_mode else "text/plain",
            },
        }
        return payload

    def _extract(self, data: dict) -> str:
        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"Gemini returned no candidates: {data}")
        parts = candidates[0].get("content", {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts)

    def complete(self, request: AIRequest) -> AIResponse:
        import time

        start = time.perf_counter()
        url = f"{self.base_url.rstrip('/')}/models/{self.model}:generateContent"
        params = {"key": self.api_key}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=self._payload(request), params=params)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # noqa: BLE001
            raise map_http_error(exc, self.name) from exc
        latency = int((time.perf_counter() - start) * 1000)
        return AIResponse(text=self._extract(data), provider=self.name, model=self.model, latency_ms=latency)

    async def complete_async(self, request: AIRequest) -> AIResponse:
        import time

        start = time.perf_counter()
        url = f"{self.base_url.rstrip('/')}/models/{self.model}:generateContent"
        params = {"key": self.api_key}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=self._payload(request), params=params)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # noqa: BLE001
            raise map_http_error(exc, self.name) from exc
        latency = int((time.perf_counter() - start) * 1000)
        return AIResponse(text=self._extract(data), provider=self.name, model=self.model, latency_ms=latency)