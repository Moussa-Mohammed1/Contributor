"""Concrete mutation engine: analysis -> AI -> apply with safeguards.

Every AI call is audited (prompt + response stored in the database) and
retried with exponential backoff. A structured logger records each phase.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from keeper.ai.base import AIRequest
from keeper.ai.factory import ProviderFactory
from keeper.ai.prompts import SYSTEM_PROMPT, build_prompt
from keeper.ai.providers.common import parse_plan
from keeper.analysis.analyzer import AnalysisReport, RepositoryAnalyzer
from keeper.config.models import RepositoryEntry
from keeper.database import repo as dataset
from keeper.mutation.base import MutationOutcome, MutationEngineProto
from keeper.mutation.safeguards import MutationResult, MutationSafeguards
from keeper.utils.retry import RetryExhaustedError, retry

logger = logging.getLogger(__name__)


class MutationEngine(MutationEngineProto):
    """Runs the full improvement pipeline for one commit slot."""

    def __init__(
        self,
        analyzer: RepositoryAnalyzer,
        provider_factory: ProviderFactory,
        safeguards: MutationSafeguards,
    ) -> None:
        self._analyzer = analyzer
        self._providers = provider_factory
        self._safeguards = safeguards

    def propose(
        self,
        repo: RepositoryEntry,
        *,
        dry_run: bool = False,
        previous_messages: list[str] | None = None,
        slot_id: int | None = None,
        session: Session | None = None,
    ) -> MutationOutcome:
        """Analyze the repository, ask the AI and apply the proposal."""
        outcome = MutationOutcome()

        analysis = self._analyzer.analyze(repo.path, ignore_patterns=repo.ignore_patterns)
        outcome.analysis = analysis

        provider = self._providers.resolve(repo)
        outcome.provider = provider.name
        outcome.model = provider.model

        prompt = build_prompt(
            analysis.to_prompt_json(),
            previous_messages=previous_messages or [],
            dry_run=dry_run,
        )
        request = AIRequest(
            prompt=prompt,
            system=SYSTEM_PROMPT.format(repo_name=repo.name),
            temperature=provider.temperature,
            max_tokens=provider.max_tokens,
            json_mode=True,
        )

        try:
            response = self._call_with_retry(provider, request, repo)
        except RetryExhaustedError as exc:
            self._record_prompt(session, slot_id, repo, prompt, None, False, 0)
            raise
        outcome.latency_ms = response.latency_ms
        self._record_prompt(
            session, slot_id, repo, prompt, response.text[:4000], True, response.latency_ms
        )
        logger.info(
            "AI %s (%s) responded in %dms",
            provider.name,
            provider.model or "-",
            response.latency_ms,
        )

        plan = parse_plan(response.text, repo_root=repo.path)
        outcome.plan = plan
        if plan is None or not plan.changes:
            outcome.skipped_reason = "AI reported no genuine improvement opportunity"
            logger.info("Slot skipped: %s", outcome.skipped_reason)
            return outcome

        applied = self._safeguards.apply(repo.path, plan, dry_run=dry_run)
        outcome.applied = applied
        if applied.empty and applied.rejected:
            outcome.skipped_reason = "All proposed changes were rejected by safeguards"
            logger.warning(
                "All changes rejected: %s", "; ".join(applied.rejected)
            )
        return outcome

    def rollback(self, repo: RepositoryEntry, result: MutationResult) -> None:
        """Roll back an applied mutation."""
        self._safeguards.rollback(repo.path, result)

    # ------------------------------------------------------------------

    def _call_with_retry(self, provider, request: AIRequest, repo: RepositoryEntry):
        @retry(
            attempts=3,
            base_delay=1.0,
            max_delay=15.0,
            exceptions=(Exception,),
            on_retry=lambda attempt, exc: logger.warning(
                "AI call attempt %d failed for %s: %s", attempt, repo.name, exc
            ),
        )
        def _call():
            started = time.perf_counter()
            response = provider.complete(request)
            if not response.text.strip():
                raise RuntimeError("AI returned an empty response")
            return response

        return _call()

    def _record_prompt(
        self,
        session: Session | None,
        slot_id: int | None,
        repo: RepositoryEntry,
        prompt: str,
        response: str | None,
        success: bool,
        latency_ms: int,
    ) -> None:
        if session is None:
            return
        try:
            dataset.record_prompt(
                session,
                slot_id=slot_id,
                repo_id=None,
                provider=self._providers.configure_for(repo).value,
                model=None,
                prompt=prompt[:4000],
                response=response,
                success=success,
                latency_ms=latency_ms,
            )
        except Exception:  # noqa: BLE001
            logger.warning("Could not record AI prompt audit row", exc_info=True)


def analyze_only(repo: RepositoryEntry, analyzer: RepositoryAnalyzer) -> AnalysisReport:
    """Standalone analysis helper used by the CLI and doctor."""
    return analyzer.analyze(repo.path, ignore_patterns=repo.ignore_patterns)