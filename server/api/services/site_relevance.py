"""Extensible rules for deterministic generic website surface filtering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class GenericSurfaceRule:
    category: str
    markers: tuple[str, ...]


_RULES = (
    GenericSurfaceRule(
        category="generic_file_preview",
        markers=("kkfileview", "在线文件预览", "file preview demo"),
    ),
    GenericSurfaceRule(
        category="generic_api_console",
        markers=("swagger ui", "openapi explorer", "knife4j"),
    ),
    GenericSurfaceRule(
        category="generic_middleware_console",
        markers=(
            "apache tomcat",
            "rabbitmq management",
            "minio console",
            "nacos console",
        ),
    ),
)


def classify_generic_surface(*values: object) -> str:
    haystack = " ".join(str(value or "").casefold() for value in values)
    for rule in _RULES:
        if any(marker in haystack for marker in rule.markers):
            return rule.category
    return ""


def classify_candidate_surface(
    *,
    url: str = "",
    title: str = "",
    fingerprints: Iterable[str] = (),
) -> str:
    return classify_generic_surface(url, title, *fingerprints)
