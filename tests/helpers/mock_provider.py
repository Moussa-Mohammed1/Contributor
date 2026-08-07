"""Deterministic AI provider for tests: returns a fixed, valid improvement plan.

The mock mimics a real LLM response (raw JSON text) so the whole pipeline
(parse -> validate -> apply -> safety -> commit) runs without network access.
"""

from __future__ import annotations

import json

from keeper.ai.base import AIProvider, AIRequest, AIResponse

IMPROVED_APP_PY = '''\
"""Demo module used by the contributor integration tests."""

from __future__ import annotations


def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


def multiply(a: int, b: int) -> int:
    """Return the product of two integers."""
    return a * b


def greet(name: str) -> str:
    """Return a friendly greeting."""
    return f"Hello, {name}!"


if __name__ == "__main__":
    print(greet("world"))
'''


def mock_plan_payload(*, file: str = "app.py", message: str = "feat: add multiply helper") -> str:
    """The exact JSON payload the mock provider returns."""
    plan = {
        "category": "feat",
        "message": message,
        "explanation": "Adds a multiply helper alongside the existing add helper.",
        "changes": [
            {
                "file": file,
                "action": "edit",
                "description": "Add a multiply function and keep the module self-consistent.",
                "content": IMPROVED_APP_PY,
            }
        ],
    }
    return json.dumps(plan)


class MockProvider(AIProvider):
    """An AIProvider that never touches the network."""

    name = "mock"

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        super().__init__(**kwargs)
        self.calls: list[AIRequest] = []

    def is_configured(self) -> bool:
        return True

    def complete(self, request: AIRequest) -> AIResponse:
        self.calls.append(request)
        return AIResponse(
            text=mock_plan_payload(),
            provider=self.name,
            model="mock-1",
            latency_ms=1,
        )

    def complete_async(self, request: AIRequest) -> AIResponse:
        return self.complete(request)
