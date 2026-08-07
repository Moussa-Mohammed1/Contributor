"""Domain exceptions for the contributor application.

Each layer raises the most specific exception available so that the CLI, GUI
and API can map them to friendly messages.
"""

from __future__ import annotations


class ContributorError(Exception):
    """Base class for all application-specific errors."""


class ConfigError(ContributorError):
    """Raised when the configuration is missing, invalid or inconsistent."""


class DatabaseError(ContributorError):
    """Raised when the state database cannot be opened or written."""


class RepositoryError(ContributorError):
    """Raised for repository-level problems (missing, not a git repo, ...)."""


class GitOperationError(ContributorError):
    """Raised when a git operation fails."""

    def __init__(self, message: str, command: str | None = None, output: str | None = None) -> None:
        super().__init__(message)
        self.command = command
        self.output = output


class DirtyRepositoryError(GitOperationError):
    """Raised when a repository cannot be safely touched because it is dirty."""


class ConflictError(GitOperationError):
    """Raised when a merge/rebase conflict blocks the workflow."""


class ProviderError(ContributorError):
    """Base class for AI provider errors."""


class ProviderNotConfiguredError(ProviderError):
    """Raised when the selected AI provider lacks credentials."""


class ProviderUnavailableError(ProviderError):
    """Raised when the provider endpoint cannot be reached."""


class AIResponseError(ProviderError):
    """Raised when the AI response cannot be parsed or violates the schema."""


class MutationRejectedError(ContributorError):
    """Raised when a proposed mutation is rejected by the safeguards."""


class SafetyCheckError(ContributorError):
    """Raised when a post-change safety check (linter/tests) fails."""


class PlannerError(ContributorError):
    """Raised when a day plan cannot be generated."""


class StateError(ContributorError):
    """Raised when execution state is inconsistent."""
