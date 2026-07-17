"""Registry-backed generation of downloadable text-based artifacts."""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from functools import partial
from typing import Any, Callable


@dataclass(frozen=True)
class ArtifactFormat:
    name: str
    suffix: str
    content_type: str
    encoder: Callable[[str], bytes]


def _utf8(value: str) -> bytes:
    return value.encode("utf-8")


def _csv_utf8(value: str) -> bytes:
    # UTF-8 BOM keeps Chinese column names readable in desktop spreadsheet apps.
    return b"\xef\xbb\xbf" + value.encode("utf-8")


def _json_utf8(value: str) -> bytes:
    parsed = json.loads(value)
    return json.dumps(parsed, ensure_ascii=False, indent=2).encode("utf-8")


ARTIFACT_FORMATS: dict[str, ArtifactFormat] = {
    "markdown": ArtifactFormat("markdown", ".md", "text/markdown; charset=utf-8", _utf8),
    "text": ArtifactFormat("text", ".txt", "text/plain; charset=utf-8", _utf8),
    "json": ArtifactFormat("json", ".json", "application/json; charset=utf-8", _json_utf8),
    "csv": ArtifactFormat("csv", ".csv", "text/csv; charset=utf-8", _csv_utf8),
}


def _safe_filename(title: str) -> str:
    base = re.sub(r"[^\w\u4e00-\u9fa5\-]+", "_", (title or "document").strip())
    return (base.strip("_") or "document")[:60]


def generate_text_artifact(*, title: str, content: str, output_format: str) -> dict[str, Any]:
    """Generate one validated in-memory artifact through the format registry."""
    format_name = str(output_format or "").strip().lower()
    provider = ARTIFACT_FORMATS.get(format_name)
    if provider is None:
        raise ValueError(
            f"不支持的文本产物格式 {format_name!r}；可用格式：{', '.join(ARTIFACT_FORMATS)}"
        )
    if not str(content or "").strip():
        raise ValueError("产物正文不能为空")
    try:
        data = provider.encoder(str(content))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 正文格式错误：{exc.msg}") from exc

    artifact_id = "art_" + uuid.uuid4().hex[:20]
    return {
        "artifact_id": artifact_id,
        "data": data,
        "filename": f"{_safe_filename(title)}{provider.suffix}",
        "size": len(data),
        "title": title or "未命名文档",
        "kind": provider.name,
        "content_type": provider.content_type,
    }


def _generate_word(*, title: str, content: str) -> dict[str, Any]:
    from api.services.artifact_word import generate_docx

    return generate_docx(title=title, content=content)


def _generate_registered_text(
    output_format: str,
    *,
    title: str,
    content: str,
) -> dict[str, Any]:
    return generate_text_artifact(
        title=title,
        content=content,
        output_format=output_format,
    )


ARTIFACT_GENERATORS: dict[str, Callable[..., dict[str, Any]]] = {
    "word": _generate_word,
    **{
        name: partial(_generate_registered_text, name)
        for name in ARTIFACT_FORMATS
    },
}

ARTIFACT_FORMAT_ALIASES = {
    "doc": "word",
    "docx": "word",
    "md": "markdown",
    "txt": "text",
}


def supported_formats() -> tuple[str, ...]:
    return tuple(ARTIFACT_GENERATORS)


def normalize_artifact_format(output_format: str) -> str:
    format_name = str(output_format or "word").strip().lower().lstrip(".")
    return ARTIFACT_FORMAT_ALIASES.get(format_name, format_name)


def generate_artifact(*, title: str, content: str, output_format: str) -> dict[str, Any]:
    """Generate any supported document through one registry entry point."""
    format_name = normalize_artifact_format(output_format)
    provider = ARTIFACT_GENERATORS.get(format_name)
    if provider is None:
        raise ValueError(
            f"不支持的产物格式 {format_name!r}；可用格式：{', '.join(supported_formats())}"
        )
    return provider(title=title, content=content)
