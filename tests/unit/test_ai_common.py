"""AI response parsing: JSON extraction, plan validation and error mapping."""

from __future__ import annotations

import pytest

from keeper.ai.base import AIImprovementPlan
from keeper.ai.providers.common import extract_json_object, map_http_error, parse_plan
from keeper.core.exceptions import AIResponseError, ProviderUnavailableError


def test_extract_plain_json() -> None:
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_fenced_json() -> None:
    text = 'Here you go:\n```json\n{"a": 2}\n```\nThanks!'
    assert extract_json_object(text) == {"a": 2}


def test_extract_from_noisy_text() -> None:
    text = 'Explanation prose. {"a": 3} trailing prose'
    assert extract_json_object(text) == {"a": 3}


def test_extract_invalid_raises() -> None:
    with pytest.raises(AIResponseError):
        extract_json_object("no json here")


def test_parse_plan_valid() -> None:
    payload = {
        "category": "docs",
        "message": "docs: clarify usage",
        "explanation": "clearer",
        "changes": [
            {"file": "README.md", "action": "edit", "description": "x", "content": "# New"}
        ],
    }
    import json

    plan = parse_plan(json.dumps(payload))
    assert isinstance(plan, AIImprovementPlan)
    assert plan.category.value == "docs"
    assert len(plan.changes) == 1


def test_parse_plan_declines() -> None:
    import json

    payload = {"category": "chore", "message": "chore: nothing", "explanation": "", "changes": []}
    assert parse_plan(json.dumps(payload)) is None


def test_parse_plan_invalid_category() -> None:
    import json

    payload = {
        "category": "banana",
        "message": "banana: nope",
        "explanation": "x",
        "changes": [{"file": "a", "action": "edit", "description": "x", "content": "y"}],
    }
    with pytest.raises(AIResponseError):
        parse_plan(json.dumps(payload))


def test_parse_plan_unsafe_path() -> None:
    import json

    payload = {
        "category": "chore",
        "message": "chore: x",
        "explanation": "x",
        "changes": [{"file": "/etc/passwd", "action": "edit", "description": "x", "content": "y"}],
    }
    with pytest.raises(AIResponseError):
        parse_plan(json.dumps(payload))


def test_map_http_error() -> None:
    import httpx

    request = httpx.Request("POST", "http://api.example.invalid/v1/chat")
    response = httpx.Response(401, text="invalid key", request=request)
    exc = map_http_error(httpx.HTTPStatusError("401", request=request, response=response), "openai")
    assert isinstance(exc, ProviderUnavailableError)
    assert "401" in str(exc)


def test_map_http_error_passthrough() -> None:
    exc = map_http_error(ProviderUnavailableError("already friendly"), "openai")
    assert isinstance(exc, ProviderUnavailableError)
    assert str(exc) == "already friendly"
