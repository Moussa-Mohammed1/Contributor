"""ProviderFactory: selection priority, configuration checks and plugins."""

from __future__ import annotations

import pytest

from keeper.ai.base import AIProvider
from keeper.ai.factory import ProviderFactory
from keeper.config.models import AIProviderConfig, RepositoryEntry
from keeper.core.exceptions import ProviderNotConfiguredError
from keeper.core.types import ProviderName
from keeper.ai.providers.ollama_provider import OllamaProvider


@pytest.fixture(autouse=True)
def _restore_real_ollama():
    """The conftest mock registers under OLLAMA; undo it for factory tests."""
    ProviderFactory.register(ProviderName.OLLAMA, OllamaProvider)
    yield
    ProviderFactory.register(ProviderName.OLLAMA, OllamaProvider)


def _factory(**ai) -> ProviderFactory:
    config = AIProviderConfig.model_validate(ai)
    return ProviderFactory(config)


def test_auto_resolution_local_first() -> None:
    factory = _factory(
        provider="auto",
        providers={
            "openai": {"api_key": "sk-test"},
            "lmstudio": {"base_url": "http://127.0.0.1:1234"},
        },
    )
    # LM Studio must win over OpenAI because local providers come first.
    assert factory.configure_for(None) == ProviderName.LMSTUDIO


def test_explicit_provider_wins() -> None:
    factory = _factory(
        provider="openai",
        providers={"openai": {"api_key": "sk-test"}, "claude": {"api_key": "abc"}},
    )
    assert factory.configure_for(None) == ProviderName.OPENAI


def test_repo_preferred_provider_wins() -> None:
    factory = _factory(
        provider="openai",
        providers={"openai": {"api_key": "sk-test"}, "claude": {"api_key": "abc"}},
    )
    repo = RepositoryEntry(path="/tmp/x", preferred_provider=ProviderName.CLAUDE)
    assert factory.configure_for(repo) == ProviderName.CLAUDE


def test_auto_resolution_raises_when_nothing_configured() -> None:
    factory = _factory(provider="auto")
    with pytest.raises(ProviderNotConfiguredError):
        factory.configure_for(None)


def test_resolve_raises_for_unconfigured_explicit() -> None:
    factory = _factory(provider="claude")
    with pytest.raises(ProviderNotConfiguredError):
        factory.resolve(None)


def test_resolve_returns_configured_provider() -> None:
    factory = _factory(provider="openai", providers={"openai": {"api_key": "sk-test"}})
    provider = factory.resolve(None)
    assert isinstance(provider, AIProvider)
    assert provider.api_key == "sk-test"


def test_status_lists_providers() -> None:
    factory = _factory(provider="auto", providers={"ollama": {"base_url": "http://x"}})
    status = factory.status()
    assert status["ollama"]["configured"] is True
    assert status["openai"]["configured"] is False
    assert status["_active"] == "ollama"


def test_register_replaces_builder() -> None:
    class Custom(AIProvider):
        name = "custom"

        def complete(self, request):
            raise NotImplementedError

    ProviderFactory.register(ProviderName.OPENROUTER, Custom)
    try:
        factory = _factory(provider="openrouter", providers={"openrouter": {"api_key": "k"}})
        assert isinstance(factory.build(ProviderName.OPENROUTER), Custom)
    finally:
        from keeper.ai.providers.openai_compat_provider import OpenRouterProvider

        ProviderFactory.register(ProviderName.OPENROUTER, OpenRouterProvider)
