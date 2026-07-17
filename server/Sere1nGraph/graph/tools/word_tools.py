"""
通用文档产物工具 — 供 AI 中枢 ReAct Agent 调用。

这是一个「通用工具」：不限定内容主体，Agent 可将任意整理好的内容（报告、话术包、
人物背景、方案等）一键生成 .docx，并返回受登录鉴权的下载链接交给前端下载。

文件生成收敛在 api.services.artifact_files / artifact_word，元信息登记收敛在 api.dao.artifacts，
本文件仅做同步 tool 封装（通过 _run_coro_sync 调用 async DAO）。
"""
from __future__ import annotations

import json
from typing import Any

from langchain.tools import tool

from . import _refs
from .builtin import _run_coro_sync


def _persist_artifact(
    result: dict[str, Any],
    *,
    kind: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """登记产物元信息，并写入当前请求的用户/会话上下文。"""

    from api.services.artifact_context import (
        current_artifact_meta,
        get_artifact_context,
        record_created_artifact,
    )

    context = get_artifact_context()
    artifact_meta = {**current_artifact_meta(), **(meta or {})}

    async def _run() -> dict[str, Any]:
        from api.dao import artifacts as artifacts_dao
        from api.db.mongodb import get_db
        from api.storage import get_object_storage

        storage = await get_object_storage()
        stored = await storage.store_bytes(
            result["data"],
            kind=kind or result["kind"],
            filename=result["filename"],
            object_id=result["artifact_id"],
            content_type=str(result.get("content_type") or "application/octet-stream"),
            owner=context.owner if context else "",
            project_id=str(artifact_meta.get("project_id") or ""),
            conversation_id=str(artifact_meta.get("conversation_id") or ""),
            source="artifact",
            source_id=result["artifact_id"],
            meta={"title": result["title"]},
        )

        return await artifacts_dao.create_artifact(
            get_db(),
            artifact_id=result["artifact_id"],
            kind=kind or result["kind"],
            title=result["title"],
            filename=result["filename"],
            storage_object_id=stored["object_id"],
            size=result.get("size", 0),
            content_type=str(result.get("content_type") or "application/octet-stream"),
            owner=context.owner if context else "",
            meta=artifact_meta,
        )

    doc = _run_coro_sync(_run())
    record_created_artifact(
        {
            "artifact_id": doc["artifact_id"],
            "kind": doc.get("kind", "word"),
            "title": doc.get("title", ""),
            "filename": doc.get("filename", ""),
            "size": doc.get("size", 0),
            "content_type": doc.get("content_type", "application/octet-stream"),
            "download_url": doc.get("download_url", ""),
        }
    )
    return doc


def _artifact_response(doc: dict[str, Any]) -> str:
    artifact_id = str(doc.get("artifact_id") or "")
    title = str(doc.get("title") or "文档产物")
    filename = str(doc.get("filename") or artifact_id)
    kind = str(doc.get("kind") or "file")
    format_label = {
        "word": "Word",
        "payload_word": "载荷 Word",
        "markdown": "Markdown",
        "text": "TXT",
        "json": "JSON",
        "csv": "CSV",
    }.get(kind, "文件")
    url = str(doc.get("download_url") or f"/api/v1/artifacts/{artifact_id}/download")
    return (
        f"已生成 {format_label} 产物《{title}》。\n"
        f"文件名：{filename}\n"
        f"产物引用：[[artifact:{artifact_id}|{title}]]\n"
        f"下载链接：{url}"
    )


@tool(
    "generate_word_document",
    description=(
        "将整理好的文本内容生成为可下载的 Word（.docx）文档，返回下载链接。"
        "适用于把报告、话术包、人物背景、授权演练方案等任意内容导出为 Word 交给用户下载。"
        "参数：title（文档标题，必填）；content（Markdown 风格正文，支持 # 标题、- 列表）；"
        "sections（可选，结构化段落的 JSON 字符串，形如 "
        '[{"heading":"章节名","body":"正文"}]）。content 与 sections 至少提供其一。'
    ),
)
def generate_word_document(title: str, content: str = "", sections: str = "") -> str:
    """生成 Word 文档并返回下载链接。"""
    if not (title or "").strip():
        return "生成失败：title（文档标题）不能为空。"

    parsed_sections: list[dict[str, str]] | None = None
    if sections and sections.strip():
        try:
            raw = json.loads(sections)
            if isinstance(raw, list):
                parsed_sections = [
                    {
                        "heading": str(item.get("heading", "")),
                        "body": str(item.get("body", "")),
                    }
                    for item in raw
                    if isinstance(item, dict)
                ]
        except (ValueError, TypeError):
            return "生成失败：sections 需为合法的 JSON 数组字符串。"

    if not (content and content.strip()) and not parsed_sections:
        return "生成失败：content 与 sections 至少提供其一。"

    try:
        from api.services import artifact_word

        result = artifact_word.generate_docx(
            title=title,
            content=content,
            sections=parsed_sections,
        )
    except Exception as exc:  # noqa: BLE001
        return f"生成 Word 文档失败：{exc}"

    try:
        doc = _persist_artifact(
            result,
            meta={
                "content": (content or "")[:200_000],
                "sections": parsed_sections or [],
            },
        )
    except Exception as exc:  # noqa: BLE001
        return f"Word 文档已生成但登记失败：{exc}"

    return _artifact_response(doc)


@tool(
    "generate_document_artifact",
    description=(
        "把完整正文生成为可下载产物。output_format 支持 word、markdown、text、json、csv；"
        "用户未指定格式时使用 word。JSON 必须是合法 JSON，CSV 正文应包含表头。"
        "返回稳定的产物引用和受登录鉴权的下载入口。"
    ),
)
def generate_document_artifact(
    title: str,
    content: str,
    output_format: str = "word",
) -> str:
    """Generate and persist a document through the unified artifact interface."""
    title = str(title or "").strip()
    content = str(content or "")
    format_name = str(output_format or "word").strip().lower()
    if not title:
        return "生成失败：title（文档标题）不能为空。"
    if not content.strip():
        return "生成失败：content（文档正文）不能为空。"

    try:
        from api.services.artifact_files import generate_artifact, normalize_artifact_format

        format_name = normalize_artifact_format(format_name)
        result = generate_artifact(
            title=title,
            content=content,
            output_format=format_name,
        )
        doc = _persist_artifact(
            result,
            meta={"content": content[:200_000], "output_format": format_name},
        )
    except Exception as exc:  # noqa: BLE001
        return f"生成 {format_name} 产物失败：{exc}"
    return _artifact_response(doc)


@tool(
    "generate_payload_word",
    description=(
        "将已完成公网检索、平台数据查询和来源核验的内部载荷方案整理为独立 Word 产物。"
        "参数：title（标题）、content（完整 Markdown 正文）、sources（来源 JSON 数组字符串，"
        "每项可含 title/url/summary）、references（平台实体引用 JSON 数组字符串）。"
        "仅用于授权的研究、演练和内容交付，不生成可执行恶意代码。"
    ),
)
def generate_payload_word(
    title: str,
    content: str,
    sources: str = "[]",
    references: str = "[]",
) -> str:
    """生成带来源和引用元数据的载荷 Word 产物。"""
    if not (title or "").strip():
        return "生成失败：title（文档标题）不能为空。"
    if not (content or "").strip():
        return "生成失败：content（文档正文）不能为空。"

    def _parse_list(raw: str, field_name: str) -> list[dict[str, Any]]:
        try:
            value = json.loads(raw or "[]")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} 需为合法 JSON 数组字符串") from exc
        if not isinstance(value, list):
            raise ValueError(f"{field_name} 需为 JSON 数组")
        return [dict(item) for item in value if isinstance(item, dict)]

    try:
        source_items = _parse_list(sources, "sources")
        reference_items = _parse_list(references, "references")
    except ValueError as exc:
        return f"生成失败：{exc}。"

    try:
        from api.services import artifact_word

        result = artifact_word.generate_docx(title=title, content=content)
        doc = _persist_artifact(
            result,
            kind="payload_word",
            meta={
                "content": content[:200_000],
                "sources": source_items[:100],
                "references": reference_items[:100],
                "agent": "payload",
            },
        )
    except Exception as exc:  # noqa: BLE001
        return f"生成载荷 Word 失败：{exc}"

    return _artifact_response(doc)


def _persona_sections(bundle: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    """把人物上下文包组织成 Word 章节。返回 (title, sections)。"""
    person = bundle.get("person") or {}
    name = person.get("name") or "未知人物"
    title = f"人物背景报告：{name}"

    basic_lines: list[str] = []
    for label, key in (("公司", "company"), ("行业", "industry"), ("职位", "position"), ("所在地", "location")):
        val = person.get(key)
        if val:
            basic_lines.append(f"- {label}：{val}")
    if person.get("summary"):
        basic_lines.append(f"- 摘要：{person['summary']}")

    sections: list[dict[str, str]] = [
        {"heading": "基本信息", "body": "\n".join(basic_lines) or "（暂无）"},
    ]
    if person.get("background"):
        sections.append({"heading": "背景经历", "body": str(person["background"])})

    trait_lines: list[str] = []
    if person.get("personality"):
        trait_lines.append(f"- 性格：{person['personality']}")
    if person.get("interests"):
        trait_lines.append(f"- 兴趣：{', '.join(person['interests'])}")
    if person.get("tags"):
        trait_lines.append(f"- 标签：{', '.join(person['tags'])}")
    if trait_lines:
        sections.append({"heading": "性格与兴趣", "body": "\n".join(trait_lines)})

    if person.get("risk_signals"):
        sections.append(
            {"heading": "风险点", "body": "\n".join(f"- {r}" for r in person["risk_signals"])}
        )

    company = bundle.get("company") or {}
    assets = bundle.get("assets") or []
    if company or assets:
        lines: list[str] = []
        if company.get("normalized_name"):
            lines.append(f"- 规范化全称：{company['normalized_name']}")
        if company.get("root_domain"):
            lines.append(f"- 根域名：{company['root_domain']}")
        if assets:
            lines.append(f"- 关联资产：共 {bundle.get('assets_total') or len(assets)} 条，示例：")
            for a in assets[:10]:
                host = a.get("host") or a.get("domain") or a.get("ip") or ""
                if host:
                    lines.append(f"  · {host}")
        if lines:
            sections.append({"heading": "关联公司与资产", "body": "\n".join(lines)})

    findings = bundle.get("findings") or []
    if findings:
        lines = []
        for f in findings:
            label = f.get("label") or f.get("value") or f.get("finding_id") or ""
            seg = f"- {label}"
            if f.get("attention_score") is not None:
                seg += f"（关注度 {f['attention_score']}）"
            if f.get("copywriting"):
                seg += "，含话术"
            lines.append(seg)
        sections.append({"heading": "关联发现与话术", "body": "\n".join(lines)})

    return title, sections


@tool(
    "generate_persona_word",
    description=(
        "按 person_id 一键生成结构化的「人物背景报告」Word 文档并返回下载链接。"
        "自动拉取人物完整上下文（画像 + 公司元信息 + 资产 + 关联发现/话术）并组织成标准章节，"
        "无需手动整理内容。用于产出可下载的人物背景资料交付。参数：person_id（必填）。"
    ),
)
def generate_persona_word(person_id: str) -> str:
    """按 person_id 生成人物背景 Word 文档。"""
    person_id = (person_id or "").strip()
    if not person_id:
        return "生成失败：person_id 不能为空。"

    async def _load() -> dict[str, Any] | None:
        from api.db.mongodb import get_db
        from api.services import context_resolver

        return await context_resolver.resolve_person_context(get_db(), person_id)

    try:
        bundle = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"解析人物上下文失败：{exc}"

    if not bundle or not (bundle.get("person") or {}).get("name"):
        return f"未找到 person_id={person_id} 对应的人物，无法生成背景报告。"

    title, sections = _persona_sections(bundle)

    try:
        from api.services import artifact_word

        result = artifact_word.generate_docx(title=title, sections=sections)
    except Exception as exc:  # noqa: BLE001
        return f"生成人物背景 Word 失败：{exc}"

    try:
        doc = _persist_artifact(
            result,
            meta={
                "content": "\n\n".join(
                    f"# {section.get('heading', '')}\n{section.get('body', '')}"
                    for section in sections
                )[:200_000],
                "person_id": person_id,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return f"人物背景 Word 已生成但登记失败：{exc}"

    name = (bundle.get("person") or {}).get("name") or person_id
    ref = _refs.person_ref(person_id, name)
    tail = f"\n关联人物：{ref}" if ref else ""
    return f"{_artifact_response(doc)}{tail}"


# 供 Agent 复用的产物工具集
WORD_TOOLS = [generate_document_artifact, generate_word_document, generate_persona_word]
PAYLOAD_WORD_TOOLS = [generate_payload_word]
