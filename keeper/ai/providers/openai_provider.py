"""OpenAI provider (chat completions)."""

from __future__ import annotations

import httpx

from keeper.ai.base import AIProvider, AIRequest, AIResponse
from keeper.ai.providers.common import map_http_error

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE = "https://api.openai.com/v1"


class OpenAIProvider(AIProvider):
    """OpenAI chat completions via the /chat/completions endpoint."""

    name = "openai"
    requires_key = True

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("model", DEFAULT_MODEL)
        kwargs.setdefault("base_url", DEFAULT_BASE)
        super().__init__(**kwargs)

    def _payload(self, request: AIRequest) -> dict:
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if request.json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def complete(self, request: AIRequest) -> AIResponse:
        import time

        start = time.perf_counter()
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=self._payload(request), headers=headers)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # noqa: BLE001
            raise map_http_error(exc, self.name) from exc
        latency = int((time.perf_counter() - start) * 1000)
        text = data["choices"][0]["message"]["content"]
        model = data.get("model") or self.model
        return AIResponse(text=text, provider=self.name, model=model, latency_ms=latency)

    async def complete_async(self, request: AIRequest) -> AIResponse:
        import time

        start = time.perf_counter()
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=self._payload(request), headers=headers)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # noqa: BLE001
            raise map_http_error(exc, self.name) from exc
        latency = int((time.perf_counter() - start) * 1000)
        return AIResponse(
            text=data["choices"][0]["message"]["content"],
            provider=self.name,
            model=data.get("model") or self.model,
            latency_ms=latency,
        )