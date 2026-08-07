"""Shared helpers for AI providers: JSON extraction and error mapping."""

from __future__ import annotations

import json
import re
from typing import Any

from keeper.core.exceptions import AIResponseError, ProviderUnavailableError
from keeper.ai.base import AIImprovementPlan


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from an LLM response.

    Handles markdown fences, leading prose and trailing noise.
    """
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise AIResponseError(f"AI returned invalid JSON: {exc}") from exc
    raise AIResponseError("AI response contained no JSON object")


def parse_plan(text: str, *, repo_root: str = ".", max_files: int = 15) -> AIImprovementPlan | None:
    """Parse and validate an AI response into an AIImprovementPlan.

    Returns None when the model explicitly declined (empty changes list).
    """
    data = extract_json_object(text)
    changes = data.get("changes") or []
    if not changes:
        return None
    try:
        plan = AIImprovementPlan.model_validate(data)
        plan.validate_safety(repo_root, max_files)
        return plan
    except Exception as exc:  # noqa: BLE001 - pydantic and safety errors alike
        raise AIResponseError(f"AI plan validation failed: {exc}") from exc


def map_http_error(exc: Exception, provider_name: str) -> ProviderUnavailableError:
    """Convert httpx transport/status errors into a friendly exception."""
    import httpx

    if isinstance(exc, httpx.ConnectError):
        return ProviderUnavailableError(
            f"{provider_name} is unreachable ({exc}). Is the endpoint running?"
        )
    if isinstance(exc, httpx.TimeoutException):
        return ProviderUnavailableError(f"{provider_name} request timed out")
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return ProviderUnavailableError(
                f"{provider_name} rejected the API key (HTTP {status})"
            )
        if status == 429:
            return ProviderUnavailableError(f"{provider_name} rate limited (HTTP 429)")
        return ProviderUnavailableError(
            f"{provider_name} returned HTTP {status}: {exc.response.text[:300]}"
        )
    if isinstance(exc, ProviderUnavailableError):
        return exc
    return ProviderUnavailableError(f"{provider_name} request failed: {exc}")