"""Commit message building and validation."""

from __future__ import annotations

import pytest

from keeper.commit.messages import (
    build_message,
    infer_category,
    is_valid_subject,
    rollup_message,
    safe_message_from_ai,
)
from keeper.core.types import CommitCategory


def test_build_message_conventional() -> None:
    message = build_message(CommitCategory.FIX, "correct the off-by-one in pagination")
    assert message.startswith("fix: ")
    assert len(message) <= 72


def test_build_message_truncates_subject() -> None:
    long_description = "q" * 300
    message = build_message(CommitCategory.DOCS, long_description)
    assert len(message) <= 72


def test_build_message_empty_subject_fallback() -> None:
    message = build_message(CommitCategory.REFACTOR, "  ... ")
    assert message.startswith("refactor: ")


def test_build_message_with_body_and_files() -> None:
    message = build_message(
        CommitCategory.TEST,
        "add coverage for edge cases",
        body="Covers empty input and negative values.",
        changed_files=["tests/test_app.py"],
    )
    assert "Files changed:" in message
    assert "tests/test_app.py" in message


def test_is_valid_subject() -> None:
    assert is_valid_subject("fix: handle null input")
    assert not is_valid_subject("handle null input")
    assert not is_valid_subject("fix: " + "x" * 100)


def test_safe_message_from_ai_keeps_valid() -> None:
    assert safe_message_from_ai("fix", "fix: handle null input") == "fix: handle null input"


def test_safe_message_from_ai_rebuilds_invalid() -> None:
    result = safe_message_from_ai(None, "I improved the docs a little bit today")
    assert is_valid_subject(result)


def test_safe_message_from_ai_truncates() -> None:
    result = safe_message_from_ai("docs", "z" * 200)
    assert len(result) <= 72


def test_infer_category_from_words() -> None:
    assert infer_category("document the setup process") == CommitCategory.DOCS
    assert infer_category("fix the crash on startup") == CommitCategory.FIX
    assert infer_category("extract the helper") == CommitCategory.REFACTOR


def test_infer_category_from_files() -> None:
    assert infer_category("improve things", ["README.md"]) == CommitCategory.DOCS
    assert infer_category("improve things", ["requirements.txt"]) == CommitCategory.BUILD
    assert infer_category("improve things", ["test_app.py"]) == CommitCategory.TEST


def test_infer_category_default() -> None:
    assert infer_category("random words") == CommitCategory.REFACTOR


def test_rollup_message() -> None:
    message = rollup_message(CommitCategory.CHORE, "cleanup day summary", "sandbox")
    assert message.startswith("chore: ")
    assert "sandbox" in message


@pytest.mark.parametrize("value", [c.value for c in CommitCategory])
def test_all_categories_produce_valid_messages(value: str) -> None:
    message = build_message(value, f"do something for {value}")
    assert is_valid_subject(message)
