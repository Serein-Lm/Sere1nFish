"""Request-scoped context for AI generated artifacts.

Artifact tools are synchronous LangChain tools, while their callers are async
HTTP/IM workflows. Context variables keep ownership and conversation metadata
out of tool signatures and make every channel use the same persistence path.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class ArtifactContext:
    owner: str = ""
    is_admin: bool = False
    conversation_id: str = ""
    project_id: str = ""
    channel: str = "web"
    references: list[dict[str, Any]] = field(default_factory=list)
    created: list[dict[str, Any]] = field(default_factory=list)


_artifact_context: ContextVar[ArtifactContext | None] = ContextVar(
    "artifact_context", default=None
)


def get_artifact_context() -> ArtifactContext | None:
    return _artifact_context.get()


def current_artifact_meta() -> dict[str, Any]:
    context = get_artifact_context()
    if context is None:
        return {}
    return {
        "conversation_id": context.conversation_id,
        "project_id": context.project_id,
        "channel": context.channel,
        "references": list(context.references),
    }


def record_created_artifact(artifact: dict[str, Any]) -> None:
    context = get_artifact_context()
    if context is not None:
        context.created.append(dict(artifact))


@contextmanager
def artifact_context(
    *,
    owner: str = "",
    is_admin: bool = False,
    conversation_id: str = "",
    project_id: str = "",
    channel: str = "web",
    references: list[dict[str, Any]] | None = None,
) -> Iterator[ArtifactContext]:
    context = ArtifactContext(
        owner=owner,
        is_admin=is_admin,
        conversation_id=conversation_id,
        project_id=project_id,
        channel=channel,
        references=list(references or []),
    )
    token = _artifact_context.set(context)
    try:
        yield context
    finally:
        _artifact_context.reset(token)
