"""
统一上下文检索工具 — 供 AI 中枢 ReAct Agent 调用。

在生成钓鱼话术/邮件/攻击方案或 Word 产物前，Agent 可用它一次性拉取某个实体
（人物或公司）的完整上下文包：画像 + 公司元信息 + 资产 + findings(话术/资料) + 接触画像。

聚合逻辑收敛在 api.services.context_resolver，本文件仅做同步 tool 封装
（通过 _run_coro_sync 调用 async 聚合服务）。
"""
from __future__ import annotations

from typing import Any

from langchain.tools import tool

from . import _refs
from .builtin import _run_coro_sync


def _format_company(company: dict[str, Any] | None) -> list[str]:
    if not company:
        return []
    lines = ["【公司元信息】"]
    for label, key in (("规范化全称", "normalized_name"), ("根域名", "root_domain")):
        val = company.get(key)
        if val:
            lines.append(f"- {label}：{val}")
    aliases = company.get("aliases") or []
    if aliases:
        lines.append(f"- 别名：{', '.join(aliases)}")
    return lines


def _format_assets(assets: list[dict[str, Any]], total: int) -> list[str]:
    if not assets:
        return []
    lines = [f"【资产情报】共 {total} 条，示例前 {min(len(assets), 10)} 条："]
    for a in assets[:10]:
        host = a.get("host") or a.get("domain") or a.get("ip") or ""
        title = a.get("title") or ""
        lines.append(f"- {host} {('｜' + title) if title else ''}".rstrip())
    return lines


def _format_findings(findings: list[dict[str, Any]]) -> list[str]:
    if not findings:
        return []
    lines = [f"【关联发现/话术】共 {len(findings)} 条："]
    for f in findings:
        label = f.get("label") or f.get("value") or f.get("finding_id") or ""
        score = f.get("attention_score")
        cw = f.get("copywriting")
        seg = f"- {label}"
        if score is not None:
            seg += f"（关注度 {score}）"
        if cw:
            seg += "，含话术"
        ref = _refs.finding_ref(f.get("finding_id", ""), label)
        if ref:
            seg += f" {ref}"
        lines.append(seg)
    return lines


def _format_refs(refs: list[dict[str, Any]]) -> list[str]:
    """把规范化关联引用渲染为可跳转清单（按类型分组）。"""
    if not refs:
        return []
    type_label = {
        "company": "公司", "finding": "发现", "asset": "资产",
        "contact_profile": "接触画像", "person": "人物",
    }
    grouped: dict[str, list[str]] = {}
    for r in refs:
        rid = r.get("id") or (r.get("meta") or {}).get("root_domain") or ""
        seg = r.get("label") or rid
        if rid and seg != rid:
            seg += f"[{rid}]"
        grouped.setdefault(r.get("type", ""), []).append(seg)
    lines = ["【可关联跳转】"]
    for t, items in grouped.items():
        shown = items[:10]
        more = f" 等{len(items)}项" if len(items) > len(shown) else ""
        lines.append(f"- {type_label.get(t, t)}：{('、'.join(shown)) + more}")
    return lines


def _format_person_context(bundle: dict[str, Any]) -> str:
    person = bundle.get("person") or {}
    lines = [f"人物：{person.get('name', '未知')}（person_id={person.get('person_id', '')}）"]
    _pref = _refs.person_ref(person.get("person_id", ""), person.get("name", ""))
    if _pref:
        lines[0] += f" {_pref}"
    for label, key in (("公司", "company"), ("行业", "industry"), ("职位", "position"), ("所在地", "location")):
        val = person.get(key)
        if val:
            lines.append(f"- {label}：{val}")
    if person.get("summary"):
        lines.append(f"- 摘要：{person['summary']}")
    if person.get("background"):
        lines.append(f"- 背景：{person['background']}")
    if person.get("personality"):
        lines.append(f"- 性格：{person['personality']}")
    if person.get("interests"):
        lines.append(f"- 兴趣：{', '.join(person['interests'])}")
    if person.get("risk_signals"):
        lines.append(f"- 风险点：{', '.join(person['risk_signals'])}")
    if person.get("tags"):
        lines.append(f"- 标签：{', '.join(person['tags'])}")

    lines += _format_company(bundle.get("company"))
    lines += _format_assets(bundle.get("assets") or [], bundle.get("assets_total") or 0)
    lines += _format_findings(bundle.get("findings") or [])

    contacts = bundle.get("contact_profiles") or []
    if contacts:
        lines.append(f"【手机聊天画像】共 {len(contacts)} 份可参考。")
    lines += _format_refs(bundle.get("related_refs") or [])
    return "\n".join(lines)


@tool(
    "get_entity_context",
    description=(
        "一次性拉取某个实体的完整上下文包，用于生成个性化话术/邮件/攻击方案或 Word 产物。"
        "按 person_id 传入时解析：人物画像 + 所属公司元信息 + 资产情报 + 关联发现/话术 + 手机聊天画像；"
        "按 company（公司名）或 root_domain（根域名）传入时解析：公司元信息 + 资产 + 关联人物。"
        "参数：person_id（优先）、company、root_domain，三者至少提供其一。"
    ),
)
def get_entity_context(person_id: str = "", company: str = "", root_domain: str = "") -> str:
    """解析实体完整上下文并返回结构化摘要文本。"""
    if not (person_id or company or root_domain):
        return "请至少提供 person_id、company 或 root_domain 其中之一。"

    async def _load() -> dict[str, Any] | None:
        from api.db.mongodb import get_db
        from api.services import context_resolver

        db = get_db()
        if person_id:
            return await context_resolver.resolve_person_context(db, person_id)
        return await context_resolver.resolve_company_context(
            db, root_domain=root_domain, company_name=company
        )

    try:
        bundle = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"解析实体上下文失败：{exc}"

    if not bundle:
        return f"未找到 person_id={person_id} 对应的人物上下文。"

    if bundle.get("entity", {}).get("type") == "person":
        return _format_person_context(bundle)

    # 公司维度
    lines = _format_company(bundle.get("company"))
    if not lines:
        lines = [f"公司（root_domain={bundle.get('root_domain', '')}）"]
    lines += _format_assets(bundle.get("assets") or [], bundle.get("assets_total") or 0)
    related = bundle.get("related_persons") or []
    if related:
        lines.append(f"【关联人物】共 {bundle.get('related_persons_total', len(related))} 人：")
        for p in related[:10]:
            seg = p.get("name", "未知")
            if p.get("position"):
                seg += f"（{p['position']}）"
            seg += f" person_id={p.get('person_id', '')}"
            ref = _refs.person_ref(p.get("person_id", ""), p.get("name", ""))
            if ref:
                seg += f" {ref}"
            lines.append(f"- {seg}")
    lines += _format_refs(bundle.get("related_refs") or [])
    return "\n".join(lines)


# 供 Agent 复用的上下文聚合工具集
CONTEXT_TOOLS = [get_entity_context]
