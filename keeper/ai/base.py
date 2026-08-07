"""AI provider abstraction: request/response models and provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from keeper.core.types import CommitCategory


class ChangeProposal(BaseModel):
    """One file-level change proposed by the AI."""

    file: str = Field(description="Repository-relative file path")
    action: str = Field(description="One of: edit | create | delete")
    description: str = Field(description="Human-readable explanation of the change")
    content: str | None = Field(default=None, description="Full new file content for edit/create")


class AIImprovementPlan(BaseModel):
    """The full JSON contract an AI must return."""

    category: CommitCategory = Field(description="Conventional commit category")
    message: str = Field(description="Concise commit message subject (max 72 chars, conventional format)")
    explanation: str = Field(description="Why this change is a genuine improvement")
    changes: list[ChangeProposal] = Field(min_length=1, max_length=8)

    def validate_safety(self, repo_root: str, max_files: int = 15) -> None:
        """Structural checks against the repo root before anything is applied."""
        import posixpath

        if len(self.changes) > max_files:
            raise ValueError(f"Too many files ({len(self.changes)} > {max_files})")
        for change in self.changes:
            if change.action not in {"edit", "create", "delete"}:
                raise ValueError(f"Unknown action {change.action!r}")
            if change.file.startswith((".git/", "/")):
                raise ValueError(f"Unsafe file path {change.file!r}")
            if posixpath.isabs(change.file) or ".." in change.file.split("/"):
                raise ValueError(f"Unsafe file path {change.file!r}")
            if change.action in {"edit", "create"} and not change.content:
                raise ValueError(f"No content provided for {change.file!r}")


@dataclass(slots=True)
class AIRequest:
    """Input to an AI provider."""

    prompt: str
    system: str
    temperature: float = 0.7
    max_tokens: int = 2048
    json_mode: bool = True


@dataclass(slots=True)
class AIResponse:
    """Raw output of an AI provider."""

    text: str
    provider: str
    model: str | None = None
    latency_ms: int = 0


class AIProvider(ABC):
    """Abstract provider: chat completion that returns raw text."""

    name: str = "base"
    requires_key: bool = False

    def __init__(self, *, model: str | None = None, base_url: str | None = None,
                 api_key: str | None = None, temperature: float = 0.7,
                 max_tokens: int = 2048, timeout: float = 120.0) -> None:
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    @abstractmethod
    def complete(self, request: AIRequest) -> AIResponse:
        """Run a chat completion synchronously and return raw text."""

    async def complete_async(self, request: AIRequest) -> AIResponse:
        """Async variant; providers that support it should override this."""
        return self.complete(request)

    def is_configured(self) -> bool:
        """True when the provider has everything it needs to be called."""
        if self.requires_key:
            return bool(self.api_key)
        return True

    def __repr__(self) -> str:
        return f"<{type(self).__name__} model={self.model!r}>"
