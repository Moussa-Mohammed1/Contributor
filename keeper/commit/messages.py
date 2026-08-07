"""Semantic commit message generation and validation.

Messages follow the Conventional Commits shape (``category: subject``) with a
guaranteed-maximum subject length of 72 characters.
"""

from __future__ import annotations

import re

from keeper.core.types import CommitCategory

_SUBJECT_MAX = 72

_CATEGORY_WORDS: dict[CommitCategory, list[str]] = {
    CommitCategory.DOCS: ["document", "doc", "readme", "comment", "docstring", "changelog", "example"],
    CommitCategory.FIX: ["fix", "bug", "correct", "error", "crash", "broken", "fallback", "handle"],
    CommitCategory.REFACTOR: ["refactor", "extract", "simplify", "combine", "split", "rename", "dedupe"],
    CommitCategory.STYLE: ["style", "format", "sort", "import", "whitespace", "lint"],
    CommitCategory.PERF: ["perf", "perform", "speed", "optimize", "cache", "latency", "fast"],
    CommitCategory.TEST: ["test", "spec", "unit", "coverage", "fixture", "assertion"],
    CommitCategory.BUILD: ["build", "dependency", "package", "install", "upgrade", "bump", "pip", "npm", "requirements"],
    CommitCategory.CI: ["ci", "pipeline", "workflow", "action", "dockerfile", "deploy"],
    CommitCategory.CHORE: ["chore", "cleanup", "remove dead", "unused", "housekeep", "misc", "deprecat"],
}


def _infer_category(description: str, changed_files: list[str] | None = None) -> CommitCategory:
    """Guess a commit category from the natural-language description and paths."""
    lowered = description.lower()
    for category, words in _CATEGORY_WORDS.items():
        if any(w in lowered for w in words):
            return category
    if changed_files:
        if any(
            f.endswith(("package.json", "pyproject.toml", "requirements.txt", "Gemfile", "Cargo.toml"))
            for f in changed_files
        ):
            return CommitCategory.BUILD
        if "test" in lowered or any("test" in f.lower() for f in changed_files):
            return CommitCategory.TEST
        if any(f.endswith((".md", ".rst", ".txt")) for f in changed_files):
            return CommitCategory.DOCS
        if any(f.endswith((".css", ".html", ".json", ".yaml", ".yml")) for f in changed_files):
            return CommitCategory.STYLE
    return CommitCategory.REFACTOR


def infer_category(description: str, changed_files: list[str] | None = None) -> CommitCategory:
    """Public wrapper for category inference."""
    return _infer_category(description, changed_files)


def _clean_subject(text: str) -> str:
    """Normalize a subject: collapse whitespace, strip trailing punctuation."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = cleaned.rstrip(".!;:,")
    return cleaned


def build_message(
    category: CommitCategory | str,
    description: str,
    *,
    changed_files: list[str] | None = None,
    body: str | None = None,
) -> str:
    """Build a Conventional Commits message.

    The subject never exceeds 72 characters. When ``body`` is provided the
    message includes it plus an optional list of changed files.
    """
    category = CommitCategory(category)
    subject = _clean_subject(description)
    if not subject:
        subject = "quality improvements"
    full_subject = f"{category.value}: {subject}"[:_SUBJECT_MAX].rstrip()
    if not body:
        return full_subject

    file_lines = "\n".join(f"- {f}" for f in (changed_files or [])[:20])
    extra_body = f"\n\nFiles changed:\n{file_lines}" if file_lines else ""
    return f"{full_subject}\n\n{body.strip()}{extra_body}"


def is_valid_subject(subject: str) -> bool:
    """True when ``subject`` is conventional and at most 72 characters."""
    stripped = subject.strip()
    if len(stripped) > _SUBJECT_MAX:
        return False
    return bool(re.match(r"^[a-z]+:\s", stripped))


def safe_message_from_ai(category: CommitCategory | str | None, message: str, *, changed_files: list[str] | None = None) -> str:
    """Normalize an AI-provided message into a guaranteed-valid commit message.

    Keeps valid conventional messages verbatim; otherwise rebuilds one from
    the category and description.
    """
    if is_valid_subject(message):
        return message.strip()
    cleaned = _clean_subject(message)
    resolved_category = CommitCategory(category) if category else _infer_category(cleaned, changed_files)
    return build_message(resolved_category, cleaned, changed_files=changed_files)


def rollup_message(category: CommitCategory | str, description: str, repo_name: str) -> str:
    """Build a summary message for a repository's daily completion."""
    return build_message(category, f"{description} ({repo_name})")