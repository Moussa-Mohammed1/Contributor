"""AI provider factory: builds providers from configuration.

Supports explicit selection and ``auto`` resolution which picks the first
configured provider in a sane priority order (local first, then cloud).
"""

from __future__ import annotations

import logging

from keeper.ai.base import AIImprovementPlan, AIProvider, AIRequest, AIResponse
from keeper.ai.providers import (
    AnthropicProvider,
    GeminiProvider,
    LMStudioProvider,
    OllamaProvider,
    OpenAIProvider,
    OpenRouterProvider,
)
from keeper.config.models import AIProviderConfig, RepositoryEntry
from keeper.core.exceptions import ProviderNotConfiguredError
from keeper.core.types import ProviderName

logger = logging.getLogger(__name__)

_BUILDERS: dict[ProviderName, type[AIProvider]] = {
    ProviderName.OPENAI: OpenAIProvider,
    ProviderName.GEMINI: GeminiProvider,
    ProviderName.CLAUDE: AnthropicProvider,
    ProviderName.OLLAMA: OllamaProvider,
    ProviderName.LMSTUDIO: LMStudioProvider,
    ProviderName.OPENROUTER: OpenRouterProvider,
}

_AUTO_PRIORITY = [
    ProviderName.OLLAMA,
    ProviderName.LMSTUDIO,
    ProviderName.CLAUDE,
    ProviderName.OPENAI,
    ProviderName.GEMINI,
    ProviderName.OPENROUTER,
]


class ProviderFactory:
    """Constructs provider instances from configuration."""

    def __init__(self, config: AIProviderConfig) -> None:
        self._config = config

    @classmethod
    def register(cls, name: ProviderName, builder: type[AIProvider]) -> None:
        """Register or replace the builder used for ``name`` (plugin hook)."""
        _BUILDERS[name] = builder

    def build(self, name: ProviderName) -> AIProvider:
        """Build a provider by name, applying global settings + credentials."""
        builder = _BUILDERS.get(name)
        if builder is None:
            raise ProviderNotConfiguredError(f"Unsupported AI provider: {name}")
        creds = self._config.credentials_for(name)
        return builder(
            model=creds.model or None,
            base_url=creds.base_url or None,
            api_key=creds.api_key or None,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
            timeout=self._config.timeout_seconds,
        )

    def configure_for(self, repo: RepositoryEntry | None) -> ProviderName:
        """Choose the provider for a repository.

        Priority:
          1. repository.preferred_provider,
          2. ai.provider when not AUTO,
          3. auto resolution (first configured provider in priority order).
        """
        if repo is not None and repo.preferred_provider and repo.preferred_provider != ProviderName.AUTO:
            return repo.preferred_provider
        if self._config.provider != ProviderName.AUTO:
            return self._config.provider
        local = {ProviderName.OLLAMA, ProviderName.LMSTUDIO}
        for candidate in _AUTO_PRIORITY:
            if candidate in local:
                # Local servers must be explicitly configured to be "available":
                # without a base_url they silently default to a local endpoint
                # that may not exist.
                if self._config.credentials_for(candidate).base_url:
                    return candidate
                continue
            instance = self.build(candidate)
            if instance.is_configured():
                return candidate
        raise ProviderNotConfiguredError(
            "No AI provider is configured. Set an API key or start a local server "
            "(Ollama / LM Studio) and re-run `keeper doctor`."
        )

    def resolve(self, repo: RepositoryEntry | None = None) -> AIProvider:
        """Return a ready-to-use provider for the given repository."""
        name = self.configure_for(repo)
        instance = self.build(name)
        if not instance.is_configured():
            raise ProviderNotConfiguredError(
                f"Provider {name} is not configured (missing API key or endpoint)."
            )
        return instance

    def status(self) -> dict[str, dict]:
        """Describe every provider for the GUI / API (without calling them)."""
        result: dict[str, dict] = {}
        for name in _BUILDERS:
            instance = self.build(name)
            result[name.value] = {
                "configured": instance.is_configured(),
                "model": instance.model,
                "base_url": instance.base_url,
            }
        try:
            result["_active"] = self.configure_for(None).value
        except ProviderNotConfiguredError:
            result["_active"] = None
        return result


__all__ = [
    "AIImprovementPlan",
    "AIProvider",
    "AIRequest",
    "AIResponse",
    "ProviderFactory",
]