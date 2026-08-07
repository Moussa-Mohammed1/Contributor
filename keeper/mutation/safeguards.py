"""Safeguards for applying AI-proposed mutations.

Guarantees:
  * no file outside the repository root can be touched,
  * protected files (lockfiles, .git, secrets, vendored code) are off-limits,
  * whitespace-only / no-op edits are rejected before touching disk,
  * rollback is deterministic: every applied change can be reverted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from keeper.ai.base import AIImprovementPlan, ChangeProposal
from keeper.core.exceptions import MutationRejectedError

logger = logging.getLogger(__name__)

PROTECTED_NAMES = {
    ".git", ".gitignore", ".gitattributes", ".gitmodules", "package-lock.json",
    "yarn.lock", "pnpm-lock.yaml", "Gemfile.lock", "poetry.lock", "uv.lock",
    "Cargo.lock", "go.sum", "composer.lock",
}

PROTECTED_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".env", ".ini~"}

_PROTECTED_FRAGMENTS = (".git", "node_modules", "vendor", "dist", "build", "coverage", ".venv", "__pycache__")


@dataclass(slots=True)
class AppliedChange:
    """One successfully applied change (for logging and rollback)."""

    file: str
    action: str
    previous_state: str | None = None  # None = file did not exist
    bytes_written: int = 0

    def describe(self) -> str:
        if self.action == "create":
            return f"created {self.file}"
        if self.action == "delete":
            return f"deleted {self.file}"
        return f"edited {self.file} ({self.bytes_written} bytes)"


@dataclass(slots=True)
class MutationResult:
    """Outcome of applying a plan."""

    applied: list[AppliedChange] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def empty(self) -> bool:
        return not self.applied

    @property
    def changed_paths(self) -> list[str]:
        return [c.file for c in self.applied]


def _is_protected(file_name: str, repo_root: Path) -> tuple[bool, str]:
    """Return (protected?, reason) for a relative file path."""
    name = Path(file_name).name
    if name in PROTECTED_NAMES:
        return True, f"{name} is a protected file"
    if name.startswith(".") and name.endswith((".env", ".secret")):
        return True, "secret-like file"
    for suffix in PROTECTED_SUFFIXES:
        if name.endswith(suffix):
            return True, f"protected suffix {suffix}"
    lowered = file_name.lower()
    if ".git/" in lowered or lowered.startswith(".git/"):
        return True, "inside .git"
    for fragment in _PROTECTED_FRAGMENTS:
        if f"/{fragment}/" in f"/{lowered}":
            return True, f"vendored/generated path segment ({fragment})"
    return False, ""


class MutationSafeguards:
    """Validates and applies an AIImprovementPlan on disk with rollback."""

    def __init__(self, max_change_bytes: int = 512_000, allow_untracked_cleanup: bool = True) -> None:
        self._max_bytes = max_change_bytes
        self._allow_untracked_cleanup = allow_untracked_cleanup

    def apply(self, repo_root: str | Path, plan: AIImprovementPlan, *, dry_run: bool = False) -> MutationResult:
        root = Path(repo_root).resolve()
        result = MutationResult(dry_run=dry_run)

        for change in plan.changes:
            try:
                self._apply_one(root, change, result)
            except MutationRejectedError as exc:
                logger.warning("Rejected change %s: %s", change.file, exc)
                result.rejected.append(str(exc))
            except OSError as exc:
                logger.warning("Failed to apply change %s: %s", change.file, exc)
                result.rejected.append(f"{change.file}: {exc}")

        if result.empty and not result.dry_run:
            logger.info("No changes were applied for plan %r", plan.message)
        return result

    # ------------------------------------------------------------------

    def _apply_one(self, root: Path, change: ChangeProposal, result: MutationResult) -> None:
        target = self._resolve_target(root, change.file)
        protected, reason = _is_protected(change.file, root)
        if protected:
            raise MutationRejectedError(f"{change.file}: {reason}")

        if change.action == "delete":
            if not target.exists():
                raise MutationRejectedError(f"{change.file}: file does not exist")
            if not self._allow_untracked_cleanup:
                raise MutationRejectedError(f"{change.file}: untracked cleanup disabled")
            if not result.dry_run:
                target.unlink()
            result.applied.append(AppliedChange(file=change.file, action="delete"))
            logger.info("Applied delete %s", change.file)
            return

        content = change.content or ""
        if len(content.encode("utf-8")) > self._max_bytes:
            raise MutationRejectedError(
                f"{change.file}: proposed content exceeds {self._max_bytes} bytes"
            )

        if change.action == "create":
            if target.exists():
                raise MutationRejectedError(f"{change.file}: already exists, refusing to overwrite")
            if not result.dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            result.applied.append(AppliedChange(file=change.file, action="create", bytes_written=len(content)))
            logger.info("Applied create %s", change.file)
            return

        # edit
        if not target.exists():
            raise MutationRejectedError(f"{change.file}: file does not exist")
        previous = target.read_text(encoding="utf-8")
        if previous == content:
            raise MutationRejectedError(f"{change.file}: edit is a no-op (identical content)")
        if previous.strip() == content.strip() and previous != content:
            raise MutationRejectedError(f"{change.file}: whitespace-only edit rejected")
        if not result.dry_run:
            target.write_text(content, encoding="utf-8")
        result.applied.append(
            AppliedChange(file=change.file, action="edit", previous_state=previous, bytes_written=len(content))
        )
        logger.info("Applied edit %s (%d bytes)", change.file, len(content))

    def _resolve_target(self, root: Path, file_name: str) -> Path:
        """Resolve a repo-relative path and reject escapes from the root."""
        candidate = (root / file_name).resolve()
        if not candidate.is_relative_to(root):
            raise MutationRejectedError(f"{file_name}: path escapes the repository root")
        return candidate

    def rollback(self, repo_root: str | Path, result: MutationResult) -> None:
        """Restore the working tree to its pre-mutation state."""
        root = Path(repo_root).resolve()
        for applied in reversed(result.applied):
            target = root / applied.file
            try:
                if applied.action == "delete":
                    logger.info("Rollback: nothing to restore for deleted %s", applied.file)
                    continue
                if applied.previous_state is None:
                    target.unlink(missing_ok=True)
                    logger.info("Rollback: removed created file %s", applied.file)
                else:
                    target.write_text(applied.previous_state, encoding="utf-8")
                    logger.info("Rollback: restored original content of %s", applied.file)
            except OSError as exc:
                logger.error("Rollback failed for %s: %s", applied.file, exc)
        logger.warning("Rolled back %d applied changes", len(result.applied))