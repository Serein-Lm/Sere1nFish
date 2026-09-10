"""Contracts shared by Skill source adapters and the import service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class SkillResourceSpec:
    path: str
    parent_path: str
    name: str
    kind: str
    role: str
    content_type: str
    size: int
    content_hash: str
    content: str | None = None

    def as_document(self) -> dict[str, Any]:
        value = {
            "path": self.path,
            "parent_path": self.parent_path,
            "name": self.name,
            "kind": self.kind,
            "role": self.role,
            "content_type": self.content_type,
            "size": self.size,
            "content_hash": self.content_hash,
        }
        if self.content is not None:
            value["content"] = self.content
        return value


@dataclass(frozen=True)
class SkillPackage:
    slug: str
    name: str
    description: str
    content_raw: str
    category: str
    tags: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    anti_triggers: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    file_signals: list[str] = field(default_factory=list)
    risk_signals: list[str] = field(default_factory=list)
    priority: int = 5
    meta: dict[str, Any] = field(default_factory=dict)
    resources: list[SkillResourceSpec] = field(default_factory=list)


class SkillSourceAdapter(Protocol):
    source_key: str
    display_name: str

    def available(self) -> bool: ...

    def discover(self) -> list[SkillPackage]: ...
