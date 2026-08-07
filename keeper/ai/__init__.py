"""AI package: provider abstraction, factory, prompts and plan models."""

from keeper.ai.base import (
    AIImprovementPlan,
    AIProvider,
    AIRequest,
    AIResponse,
    ChangeProposal,
)
from keeper.ai.factory import ProviderFactory
from keeper.ai.prompts import SYSTEM_PROMPT, build_analysis_prompt, build_prompt
from keeper.ai.providers.common import parse_plan

__all__ = [
    "AIImprovementPlan",
    "AIProvider",
    "AIRequest",
    "AIResponse",
    "ChangeProposal",
    "ProviderFactory",
    "SYSTEM_PROMPT",
    "build_analysis_prompt",
    "build_prompt",
    "parse_plan",
]
