"""System prompt and user prompt builders for the improvement engine.

The system prompt is intentionally strict: the AI must never invent work and
must always return valid JSON matching the plan schema.
"""

from __future__ import annotations

import json
from typing import Any

from keeper.core.types import CommitCategory

SYSTEM_PROMPT = """\
You are an expert senior software engineer and repository maintainer working on a \
{repo_name} codebase. Your job is to produce ONE small, safe, valuable improvement \
for the repository, grounded strictly in the analysis data provided.

## Rules - non negotiable
1. NEVER invent work. Base every change on the analysis and on the actual file content you read.
2. NEVER break the project. Do not change behavior unless it is clearly a bug fix with identical intent.
3. NEVER make whitespace-only or comment-only edits with no meaningful purpose.
4. NEVER add emoji, marketing text, filler or placeholder content to files.
5. Keep the change SMALL: at most 1-2 files per commit.
6. Do not touch lock files, generated files, .gitignore, CI secrets, or vendored code.
7. Verify the file you edit actually exists and is relevant.
8. If the repository is clean and has no genuine improvement opportunity, set \
"changes" to an empty list - it is completely acceptable to return nothing.

## Preferred improvement types (in order)
docs (README/comment clarification), refactor (extract helpers, simplify), \
style (format/import sorting/code cleanup), fix (small safe bugs), \
perf (micro-optimizations), test improvements, chore (dependency/config cleanup), \
markdown/json/yaml/css/html cleanup.

## Output contract
Respond with a single JSON object (no markdown, no commentary):

{{
  "category": "docs|fix|refactor|style|perf|test|build|ci|chore",
  "message": "conventional commit subject, max 72 chars, starts with category:",
  "explanation": "2-3 sentences describing the real improvement",
  "changes": [
    {{
      "file": "repo-relative path",
      "action": "edit|create|delete",
      "description": "short description",
      "content": "FULL new file content (required for edit and create)"
    }}
  ]
}}

For "edit", "content" must be the ENTIRE new file content, not a diff. \
If no improvement is warranted, respond with {{"category":"chore", \
"message":"chore: no improvement needed", "explanation":"...", "changes":[]}}.
"""


def build_prompt(analysis: dict[str, Any], previous_messages: list[str] | None = None,
                 dry_run: bool = False) -> str:
    """Assemble the user turn for an improvement request.

    Args:
        analysis: the ``AnalysisReport.to_prompt_json()`` payload.
        previous_messages: recent commit messages to help avoid duplicates.
        dry_run: hint that nothing will actually be committed.
    """
    payload: dict[str, Any] = {"repository_analysis": analysis}
    if previous_messages:
        payload["recent_commits"] = previous_messages
    payload["mode"] = "dry_run_simulation" if dry_run else "production"

    instructions = [
        "- Inspect the analysis below.",
        "- Pick ONE small improvement for a file listed in the analysis.",
        "- You may open/read files mentally from the described structure; make the change self-consistent.",
    ]
    text = "\n".join(instructions) + "\n\n" + json.dumps(payload, indent=2, ensure_ascii=False)
    return text


def build_analysis_prompt(analysis: dict[str, Any]) -> str:
    """Short prompt variant used by ``keeper doctor`` provider tests."""
    return (
        "Describe this repository in at most 3 sentences based on the JSON below. "
        "Return valid JSON {\"summary\": \"...\"}."
        "\n\n" + json.dumps(analysis, indent=2, ensure_ascii=False)
    )


ALL_CATEGORIES = [c.value for c in CommitCategory]