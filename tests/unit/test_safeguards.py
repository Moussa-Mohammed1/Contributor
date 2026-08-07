"""MutationSafeguards: path safety, no-op rejection and deterministic rollback."""

from __future__ import annotations

from pathlib import Path

import pytest

from keeper.ai.base import AIImprovementPlan, ChangeProposal
from keeper.core.exceptions import MutationRejectedError
from keeper.mutation.safeguards import MutationSafeguards, PROTECTED_NAMES


def _plan(*changes: dict) -> AIImprovementPlan:
    return AIImprovementPlan(
        category="refactor",
        message="refactor: improve helper",
        explanation="genuine improvement",
        changes=[ChangeProposal(**c) for c in changes],
    )


def test_apply_create(tmp_path: Path) -> None:
    result = MutationSafeguards().apply(tmp_path, _plan(
        {"file": "lib/util.py", "action": "create", "description": "x", "content": "def f():\n    pass\n"}
    ))
    assert len(result.applied) == 1
    assert (tmp_path / "lib" / "util.py").read_text(encoding="utf-8") == "def f():\n    pass\n"


def test_apply_create_refuses_overwrite(tmp_path: Path) -> None:
    (tmp_path / "util.py").write_text("existing", encoding="utf-8")
    result = MutationSafeguards().apply(tmp_path, _plan(
        {"file": "util.py", "action": "create", "description": "x", "content": "new"}
    ))
    assert result.applied == []
    assert result.rejected
    assert (tmp_path / "util.py").read_text(encoding="utf-8") == "existing"


def test_apply_edit_noop_rejected(tmp_path: Path) -> None:
    content = "line one\n"
    (tmp_path / "app.py").write_text(content, encoding="utf-8")
    result = MutationSafeguards().apply(tmp_path, _plan(
        {"file": "app.py", "action": "edit", "description": "x", "content": content}
    ))
    assert result.applied == []
    assert "no-op" in result.rejected[0]


def test_apply_edit_whitespace_only_rejected(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("a\nb\n", encoding="utf-8")
    result = MutationSafeguards().apply(tmp_path, _plan(
        {"file": "app.py", "action": "edit", "description": "x", "content": "a\nb\n\n\n"}
    ))
    assert result.applied == []
    assert "whitespace" in result.rejected[0]


def test_apply_edit_rollback_restores(tmp_path: Path) -> None:
    original = "original content\n"
    (tmp_path / "app.py").write_text(original, encoding="utf-8")
    safeguards = MutationSafeguards()
    result = safeguards.apply(tmp_path, _plan(
        {"file": "app.py", "action": "edit", "description": "x", "content": "changed content\n"}
    ))
    assert len(result.applied) == 1
    safeguards.rollback(tmp_path, result)
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == original


def test_apply_create_rollback_removes(tmp_path: Path) -> None:
    safeguards = MutationSafeguards()
    result = safeguards.apply(tmp_path, _plan(
        {"file": "new.py", "action": "create", "description": "x", "content": "x = 1\n"}
    ))
    safeguards.rollback(tmp_path, result)
    assert not (tmp_path / "new.py").exists()


def test_apply_delete_rollback_is_noop(tmp_path: Path) -> None:
    (tmp_path / "old.py").write_text("old", encoding="utf-8")
    safeguards = MutationSafeguards()
    result = safeguards.apply(tmp_path, _plan(
        {"file": "old.py", "action": "delete", "description": "x"}
    ))
    assert not (tmp_path / "old.py").exists()
    safeguards.rollback(tmp_path, result)  # must not raise


def test_protected_files_rejected(tmp_path: Path) -> None:
    for name in list(PROTECTED_NAMES)[:5]:
        result = MutationSafeguards().apply(tmp_path, _plan(
            {"file": name, "action": "create", "description": "x", "content": "x"}
        ))
        assert result.applied == []
        assert result.rejected


def test_path_escape_rejected(tmp_path: Path) -> None:
    result = MutationSafeguards().apply(tmp_path, _plan(
        {"file": "../outside.py", "action": "create", "description": "x", "content": "x"}
    ))
    assert result.applied == []
    assert result.rejected
    assert not (tmp_path.parent / "outside.py").exists()


def test_absolute_path_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "evil.py"
    result = MutationSafeguards().apply(root, _plan(
        {"file": str(outside), "action": "create", "description": "x", "content": "x"}
    ))
    assert result.applied == []
    assert result.rejected
    assert not outside.exists()


def test_dry_run_does_not_touch_disk(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("a\n", encoding="utf-8")
    result = MutationSafeguards().apply(tmp_path, _plan(
        {"file": "app.py", "action": "edit", "description": "x", "content": "b\n"}
    ), dry_run=True)
    assert len(result.applied) == 1
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "a\n"


def test_oversized_content_rejected(tmp_path: Path) -> None:
    safeguards = MutationSafeguards(max_change_bytes=100)
    result = safeguards.apply(tmp_path, _plan(
        {"file": "big.py", "action": "create", "description": "x", "content": "x" * 500}
    ))
    assert result.applied == []
    assert result.rejected


def test_validate_safety_rejects_bad_plans(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _plan({"file": ".git/config", "action": "edit", "description": "x", "content": "x"}).validate_safety(str(tmp_path))
    with pytest.raises(ValueError):
        _plan({"file": "a.py", "action": "edit", "description": "x"}).validate_safety(str(tmp_path))
