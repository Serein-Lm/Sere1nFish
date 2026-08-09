"""Database-backed prompt access for mobile runtimes."""
from __future__ import annotations

from collections.abc import Mapping

from Sere1nGraph.graph.prompts.loader import load_prompt


def load_mobile_prompt(
    slug: str,
    replacements: Mapping[str, object] | None = None,
) -> str:
    prompt = load_prompt(slug)
    for key, value in (replacements or {}).items():
        placeholder = key if key.startswith("{{") else f"{{{{{key}}}}}"
        prompt = prompt.replace(placeholder, str(value))
    return prompt
