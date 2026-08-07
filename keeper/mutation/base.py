"""Mutation layer: base interfaces for the improvement engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from keeper.ai.base import AIImprovementPlan
from keeper.analysis.analyzer import AnalysisReport
from keeper.config.models import RepositoryEntry
from keeper.mutation.safeguards import MutationResult


@dataclass(slots=True)
class MutationOutcome:
    """Everything produced during one improvement attempt."""

    analysis: AnalysisReport | None = None
    plan: AIImprovementPlan | None = None
    applied: MutationResult | None = None
    provider: str | None = None
    model: str | None = None
    latency_ms: int = 0
    skipped_reason: str | None = None

    @property
    def has_changes(self) -> bool:
        return self.applied is not None and not self.applied.empty


class MutationEngineProto(ABC):
    """Contract implemented by the concrete mutation engine."""

    @abstractmethod
    def propose(self, repo: RepositoryEntry, *, dry_run: bool = False,
                previous_messages: list[str] | None = None,
                slot_id: int | None = None) -> MutationOutcome:
        """Analyze, ask the AI, apply the change and return the outcome."""