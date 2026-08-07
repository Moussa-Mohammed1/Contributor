"""Prompt builders: structure, escaping and dry-run hints."""

from __future__ import annotations

import json

from keeper.ai.prompts import SYSTEM_PROMPT, build_analysis_prompt, build_prompt


def test_system_prompt_is_strict() -> None:
    text = SYSTEM_PROMPT.format(repo_name="sandbox")
    assert "NEVER invent work" in text
    assert "changes" in text
    assert "sandbox" in text


def test_build_prompt_contains_analysis() -> None:
    analysis = {"language": "python", "files": ["app.py"]}
    prompt = build_prompt(analysis)
    payload = json.loads(prompt.split("\n\n", 1)[1])
    assert payload["repository_analysis"] == analysis
    assert payload["mode"] == "production"


def test_build_prompt_previous_messages() -> None:
    prompt = build_prompt({"x": 1}, previous_messages=["fix: a", "docs: b"])
    payload = json.loads(prompt.split("\n\n", 1)[1])
    assert payload["recent_commits"] == ["fix: a", "docs: b"]


def test_build_prompt_dry_run_mode() -> None:
    prompt = build_prompt({"x": 1}, dry_run=True)
    payload = json.loads(prompt.split("\n\n", 1)[1])
    assert payload["mode"] == "dry_run_simulation"


def test_build_analysis_prompt() -> None:
    prompt = build_analysis_prompt({"files": 3})
    assert "summary" in prompt
    assert json.loads(prompt.rsplit("\n\n", 1)[1]) == {"files": 3}
