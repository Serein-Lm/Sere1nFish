"""
通用 Word 文档生成工具 — 供 AI 中枢 ReAct Agent 调用。

这是一个「通用工具」：不限定内容主体，Agent 可将任意整理好的内容（报告、话术包、
人物背景、方案等）一键生成 .docx，并返回受登录鉴权的下载链接交给前端下载。

文件生成收敛在 api.services.artifact_word，元信息登记收敛在 api.dao.artifacts，
本文件仅做同步 tool 封装（通过 _run_coro_sync 调用 async DAO）。
"""
from __future__ import annotations

import json
from typing import Any

from langchain.tools import tool

from . import _refs
from .builtin import _run_coro_sync


def _persist_artifact(result: dict[str, Any]) -> str:
    """登记产物元信息并返回鉴权下载链接。"""

    async def _run() -> dict[str, Any]:
        from api.dao import artifacts as artifacts_dao
        from api.db.mongodb import get_db

        return await artifacts_dao.create_artifact(
            get_db(),
            artifact_id=result["artifact_id"],
            kind=result["kind"],
            title=result["title"],
            filename=result["filename"],
            file_path=result["file_path"],
            size=result.get("size", 0),
        )

    doc = _run_coro_sync(_run())
    return doc.get("download_url", f"/api/v1/artifacts/{result['artifact_id']}/download")


@tool(
    "generate_word_document",
    description=(
        "将整理好的文本内容生成为可下载的 Word（.docx）文档，返回下载链接。"
        "适用于把报告、话术包、人物背景、攻击方案等任意内容导出为 Word 交给用户下载。"
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
        url = _persist_artifact(result)
    except Exception as exc:  # noqa: BLE001
        return f"Word 文档已生成但登记失败：{exc}"

    return (
        f"已生成 Word 文档《{result['title']}》。\n"
        f"文件名：{result['filename']}\n"
        f"下载链接：{url}"
    )


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
        url = _persist_artifact(result)
    except Exception as exc:  # noqa: BLE001
        return f"人物背景 Word 已生成但登记失败：{exc}"

    name = (bundle.get("person") or {}).get("name") or person_id
    ref = _refs.person_ref(person_id, name)
    tail = f"\n关联人物：{ref}" if ref else ""
    return (
        f"已生成人物背景报告《{result['title']}》。\n"
        f"文件名：{result['filename']}\n"
        f"下载链接：{url}{tail}"
    )


# 供 Agent 复用的产物工具集
WORD_TOOLS = [generate_word_document, generate_persona_word]
