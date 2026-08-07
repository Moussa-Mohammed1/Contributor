"""OpenAI-compatible providers: LM Studio, OpenRouter and any /v1 endpoint.

Both providers reuse the exact same wire protocol; the only difference is the
base URL, so a single implementations class parameterized by name is used.
"""

from __future__ import annotations

import httpx

from keeper.ai.base import AIProvider, AIRequest, AIResponse
from keeper.ai.providers.common import map_http_error

DEFAULT_LMSTUDIO_BASE = "http://localhost:1234/v1"
DEFAULT_OPENROUTER_BASE = "https://openrouter.ai/api/v1"


class OpenAICompatProvider(AIProvider):
    """Generic OpenAI /v1/chat/completions client."""

    name = "openai-compat"
    requires_key = False

    def __init__(self, *, extra_headers: dict[str, str] | None = None, **kwargs) -> None:
        self._extra_headers = extra_headers or {}
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

    def _extract(self, data: dict) -> str:
        if "choices" in data:
            message = data["choices"][0].get("message") or {}
            content = message.get("content") or message.get("text") or ""
            return content
        # Some local servers respond with {content: ...}
        content = data.get("content")
        if isinstance(content, str):
            return content
        if isinstance(data.get("response"), str):
            return data["response"]
        raise RuntimeError(f"Unexpected response shape from {self.name}: {list(data.keys())}")

    def complete(self, request: AIRequest) -> AIResponse:
        import time

        start = time.perf_counter()
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers: dict[str, str] = {"content-type": "application/json", **self._extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
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
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers: dict[str, str] = {"content-type": "application/json", **self._extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=self._payload(request), headers=headers)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # noqa: BLE001
            raise map_http_error(exc, self.name) from exc
        latency = int((time.perf_counter() - start) * 1000)
        return AIResponse(text=self._extract(data), provider=self.name, model=self.model, latency_ms=latency)


class LMStudioProvider(OpenAICompatProvider):
    """LM Studio local server (OpenAI-compatible)."""

    name = "lmstudio"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("base_url", DEFAULT_LMSTUDIO_BASE)
        super().__init__(**kwargs)


class OpenRouterProvider(OpenAICompatProvider):
    """OpenRouter gateway (OpenAI-compatible)."""

    name = "openrouter"
    requires_key = True

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("base_url", DEFAULT_OPENROUTER_BASE)
        super().__init__(
            extra_headers={"HTTP-Referer": "https://github.com/contributor", "X-Title": "contributor"},
            **kwargs,
        )