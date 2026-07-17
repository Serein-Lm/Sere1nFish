"""
只读数据查询工具 — 供 AI 中枢（个人助手）ReAct Agent 实时读取平台数据。

覆盖项目/任务、finding 明细与话术、手机联系人画像与操作记录、历史会话、资产等。
Agent 可自主组合这些工具回答「看某项目进展」「查某 finding 的话术」「列出高价值目标」等问题，
并在回复中用 [[ref:...]] 标记生成可跳转引用，便于中台一键打开对应页面。

设计原则：
- 全部只读，不写库、不触发任务；底层读取收敛在既有 DAO/service。
- 本文件仅做同步 tool 封装（通过 _run_coro_sync 调用 async DAO/service）。
- 返回结构化摘要文本 + 稳定领域标识（project_id/finding_id/person_id），
  对可跳转实体内嵌引用标记（_refs）。
"""
from __future__ import annotations

import json
from typing import Any

from langchain.tools import tool

from . import _refs
from .builtin import _run_coro_sync


def _oid_str(doc: dict[str, Any]) -> str:
    """从 Mongo 文档提取字符串形式的 _id / id。"""
    raw = doc.get("_id") or doc.get("id") or ""
    return str(raw)


def _truncate(text: str, limit: int = 600) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _execution_owner() -> str:
    from api.services.artifact_context import get_artifact_context

    context = get_artifact_context()
    return str(context.owner or "").strip() if context else ""


# ============ 项目与任务 ============

@tool(
    "list_projects",
    description=(
        "列出平台内的项目（只读），按创建时间倒序，含名称、目标、描述。"
        "用于回答「有哪些项目」「帮我找某个项目」。参数：limit（默认 20，上限 50）。"
    ),
)
def list_projects(limit: int = 20) -> str:
    """列出项目清单并返回带跳转引用的摘要。"""

    async def _load() -> tuple[list[dict[str, Any]], int]:
        from api.dao import projects as projects_dao
        from api.db.mongodb import get_db

        return await projects_dao.list_projects(
            get_db(), limit=max(1, min(int(limit or 20), 50))
        )

    try:
        items, total = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"列出项目失败：{exc}"

    if not items:
        return "当前没有任何项目。"

    lines = [f"共 {total} 个项目，返回前 {len(items)} 个："]
    for idx, p in enumerate(items, 1):
        pid = _oid_str(p)
        name = p.get("name") or "(未命名)"
        target = p.get("target") or ""
        desc = p.get("description") or ""
        ref = _refs.project_ref(pid, name)
        seg = f"{idx}. {name}"
        if target:
            seg += f"（目标：{target}）"
        elif desc:
            seg += f"（{_truncate(desc, 60)}）"
        if ref:
            seg += f" {ref}"
        lines.append(seg)
    return "\n".join(lines)


@tool(
    "get_project",
    description=(
        "获取单个项目的基础信息（只读）：名称、目标、描述、累积内容条目。"
        "用于回答「这个项目是做什么的」。参数：project_id（必填）。"
        "注意：若要项目的发现统计/任务态势，请用 get_project_dashboard。"
    ),
)
def get_project(project_id: str) -> str:
    """获取单个项目基础信息。"""
    project_id = (project_id or "").strip()
    if not project_id:
        return "请提供 project_id。"

    async def _load() -> dict[str, Any] | None:
        from api.dao import projects as projects_dao
        from api.db.mongodb import get_db

        return await projects_dao.get_project(get_db(), project_id)

    try:
        p = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"获取项目失败：{exc}"
    if not p:
        return f"未找到项目 {project_id}。"

    name = p.get("name") or "(未命名)"
    lines = [f"项目：{name} {_refs.project_ref(project_id, name)}"]
    if p.get("target"):
        lines.append(f"- 目标：{p['target']}")
    if p.get("description"):
        lines.append(f"- 描述：{_truncate(p['description'], 300)}")
    contents = p.get("contents") or []
    if contents:
        lines.append(f"- 累积内容：{len(contents)} 条")
        for c in contents[-3:]:
            lines.append(f"  · {_truncate(str(c), 120)}")
    return "\n".join(lines)


@tool(
    "list_task_logs",
    description=(
        "查询任务运行日志（只读），按时间倒序。用于回答「某项目/任务最近发生了什么」"
        "「有没有报错」。参数：project_id（可选）；task_id（可选）；"
        "min_level（可选：debug/info/notice/warning/error，取该级别及以上）；limit（默认 20，上限 50）。"
    ),
)
def list_task_logs(
    project_id: str = "",
    task_id: str = "",
    min_level: str = "",
    limit: int = 20,
) -> str:
    """查询任务日志并返回结构化摘要。"""

    async def _load() -> tuple[list[dict[str, Any]], int]:
        from api.dao import task_logs as task_logs_dao
        from api.db.mongodb import get_db

        return await task_logs_dao.query_logs(
            get_db(),
            project_id=(project_id or "").strip(),
            task_id=(task_id or "").strip(),
            min_level=(min_level or "").strip(),
            limit=max(1, min(int(limit or 20), 50)),
        )

    try:
        items, total = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"查询任务日志失败：{exc}"

    if not items:
        return "未查询到符合条件的日志。"

    lines = [f"命中 {total} 条日志，返回前 {len(items)} 条（时间倒序）："]
    for it in items:
        level = (it.get("level") or "").upper()
        event = it.get("event") or ""
        msg = it.get("message") or it.get("msg") or ""
        tid = it.get("task_id") or ""
        seg = f"[{level}] {event}"
        if msg:
            seg += f"：{_truncate(str(msg), 160)}"
        if tid:
            seg += f"（task {tid}）"
        lines.append(seg)
    return "\n".join(lines)


# ============ Finding 明细与话术 ============

@tool(
    "get_finding_detail",
    description=(
        "获取单个 finding 的完整明细（只读）：来源、类型、渠道、标签/值、关注度、关注原因、URL。"
        "用于深入了解某个高价值目标。参数：finding_id（必填）。"
        "如需该 finding 的画像用 get_finding_profile，已生成话术用 get_finding_copywriting。"
    ),
)
def get_finding_detail(finding_id: str) -> str:
    """获取单条 finding 明细。"""
    finding_id = (finding_id or "").strip()
    if not finding_id:
        return "请提供 finding_id。"

    async def _load() -> dict[str, Any] | None:
        from api.dao import findings as findings_dao
        from api.db.mongodb import get_db

        return await findings_dao.get_finding(get_db(), finding_id)

    try:
        f = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"获取 finding 失败：{exc}"
    if not f:
        return f"未找到 finding {finding_id}。"

    label = f.get("label") or f.get("value") or finding_id
    lines = [f"Finding：{label} {_refs.finding_ref(finding_id, label)}"]
    meta = "／".join(x for x in (f.get("source"), f.get("type"), f.get("channel")) if x)
    if meta:
        lines.append(f"- 分类：{meta}")
    if f.get("value") and f.get("value") != label:
        lines.append(f"- 值：{_truncate(str(f['value']), 200)}")
    if f.get("attention_score") is not None:
        lines.append(f"- 关注度：{f['attention_score']}")
    if f.get("attention_reason"):
        lines.append(f"- 关注原因：{_truncate(str(f['attention_reason']), 200)}")
    if f.get("url"):
        lines.append(f"- 链接：{f['url']}")
    if f.get("project_id"):
        lines.append(f"- 所属项目：{f['project_id']}")
    return "\n".join(lines)


@tool(
    "get_finding_copywriting",
    description=(
        "获取某个 finding 已生成的社工话术（只读，若存在）。用于查阅之前生成的多渠道话术成品。"
        "参数：finding_id（必填）。"
    ),
)
def get_finding_copywriting(finding_id: str) -> str:
    """获取 finding 已生成的话术。"""
    finding_id = (finding_id or "").strip()
    if not finding_id:
        return "请提供 finding_id。"

    async def _load() -> dict[str, Any] | None:
        from api.dao import findings as findings_dao
        from api.db.mongodb import get_db

        return await findings_dao.get_copywriting(get_db(), finding_id)

    try:
        cw = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"获取话术失败：{exc}"
    if not cw:
        return f"finding {finding_id} 暂无已生成的话术。"

    payload = cw.get("copywriting") or cw.get("content") or cw.get("data") or cw
    if isinstance(payload, (dict, list)):
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        text = str(payload)
    return f"finding {finding_id} 的已生成话术 {_refs.finding_ref(finding_id)}：\n{_truncate(text, 2000)}"


@tool(
    "get_finding_profile",
    description=(
        "获取某个 finding 的目标画像（只读，若存在）：身份、性格、攻击面、标签、常用语、风险信号等。"
        "参数：finding_id（必填）。"
    ),
)
def get_finding_profile(finding_id: str) -> str:
    """获取 finding 的画像。"""
    finding_id = (finding_id or "").strip()
    if not finding_id:
        return "请提供 finding_id。"

    async def _load() -> dict[str, Any] | None:
        from api.dao import findings as findings_dao
        from api.db.mongodb import get_db

        return await findings_dao.get_profile(get_db(), finding_id)

    try:
        p = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"获取画像失败：{exc}"
    if not p:
        return f"finding {finding_id} 暂无画像。"

    lines = [f"finding {finding_id} 画像 {_refs.finding_ref(finding_id)}："]
    for key, title in (
        ("nickname", "昵称"),
        ("platform", "平台"),
        ("communication_style", "沟通风格"),
        ("tone", "语气"),
        ("reply_pattern", "回复模式"),
    ):
        if p.get(key):
            lines.append(f"- {title}：{p[key]}")
    if p.get("tags"):
        lines.append("- 标签：" + "、".join(str(t) for t in p["tags"][:12]))
    if p.get("common_phrases"):
        lines.append("- 常用语：" + "、".join(str(t) for t in p["common_phrases"][:8]))
    if p.get("risk_signals"):
        lines.append("- 风险信号：" + "、".join(str(t) for t in p["risk_signals"][:8]))
    for key, title in (
        ("identity", "身份"),
        ("personality_profile", "性格"),
        ("attack_surface", "攻击面"),
    ):
        val = p.get(key)
        if isinstance(val, dict) and val:
            lines.append(f"- {title}：{_truncate(json.dumps(val, ensure_ascii=False), 300)}")
    return "\n".join(lines)


# ============ 手机与联系人 ============

@tool(
    "list_contact_profiles",
    description=(
        "列出手机侧联系人画像（只读），可按项目或设备过滤。用于回答「手机上采集了哪些联系人」。"
        "参数：project_id（可选）；device_id（可选）；limit（默认 20，上限 50）。"
    ),
)
def list_contact_profiles(
    project_id: str = "",
    device_id: str = "",
    limit: int = 20,
) -> str:
    """列出联系人画像摘要。"""

    async def _load() -> list[dict[str, Any]]:
        from api.dao import contact_profiles as cp_dao
        from api.db.mongodb import get_db

        return await cp_dao.list_profiles(
            get_db(),
            project_id=(project_id or "").strip() or None,
            device_id=(device_id or "").strip() or None,
            limit=max(1, min(int(limit or 20), 50)),
        )

    try:
        items = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"列出联系人画像失败：{exc}"
    if not items:
        return "未查询到符合条件的联系人画像。"

    lines = [f"返回 {len(items)} 个联系人画像："]
    for idx, c in enumerate(items, 1):
        nick = c.get("nickname") or c.get("contact_id") or "(未知)"
        platform = c.get("platform") or ""
        cid = c.get("contact_id") or ""
        seg = f"{idx}. {nick}"
        if platform:
            seg += f"（{platform}）"
        if cid:
            seg += f" contact_id={cid}"
        lines.append(seg)
    return "\n".join(lines)


@tool(
    "get_contact_profile",
    description=(
        "获取单个手机联系人的完整画像（只读）：昵称、平台、人设、关联项目/finding。"
        "参数：contact_id（必填）。"
    ),
)
def get_contact_profile(contact_id: str) -> str:
    """获取单个联系人画像。"""
    contact_id = (contact_id or "").strip()
    if not contact_id:
        return "请提供 contact_id。"

    async def _load() -> dict[str, Any] | None:
        from api.dao import contact_profiles as cp_dao
        from api.db.mongodb import get_db

        return await cp_dao.get_profile(get_db(), contact_id)

    try:
        c = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"获取联系人画像失败：{exc}"
    if not c:
        return f"未找到联系人 {contact_id}。"

    nick = c.get("nickname") or contact_id
    lines = [f"联系人：{nick}（contact_id={contact_id}）"]
    if c.get("platform"):
        lines.append(f"- 平台：{c['platform']}")
    if c.get("device_id"):
        lines.append(f"- 设备：{c['device_id']}")
    persona = c.get("persona")
    if isinstance(persona, dict) and persona:
        lines.append(f"- 人设：{_truncate(json.dumps(persona, ensure_ascii=False), 400)}")
    links = c.get("project_links") or []
    if links:
        lines.append(f"- 关联项目/finding：{len(links)} 条")
        for lk in links[:5]:
            fid = lk.get("finding_id") or ""
            pid = lk.get("project_id") or ""
            seg = "  · "
            if pid:
                seg += f"项目 {pid} "
            if fid:
                seg += _refs.finding_ref(fid, fid)
            lines.append(seg)
    return "\n".join(lines)


@tool(
    "list_mobile_operations",
    description=(
        "查询手机自动化操作记录（只读），按时间倒序。用于回答「手机最近做了哪些操作」。"
        "参数：project_id（可选）；device_id（可选）；contact_id（可选）；limit（默认 20，上限 50）。"
    ),
)
def list_mobile_operations(
    project_id: str = "",
    device_id: str = "",
    contact_id: str = "",
    limit: int = 20,
) -> str:
    """查询手机操作记录摘要。"""

    async def _load() -> list[dict[str, Any]]:
        from api.dao import mobile_artifacts as mobile_dao
        from api.db.mongodb import get_db

        return await mobile_dao.list_operations(
            get_db(),
            project_id=(project_id or "").strip() or None,
            device_id=(device_id or "").strip() or None,
            contact_id=(contact_id or "").strip() or None,
            limit=max(1, min(int(limit or 20), 50)),
        )

    try:
        items = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"查询手机操作记录失败：{exc}"
    if not items:
        return "未查询到符合条件的手机操作记录。"

    lines = [f"返回 {len(items)} 条手机操作记录（时间倒序）："]
    for it in items:
        action = it.get("action") or it.get("event") or it.get("type") or "操作"
        detail = it.get("detail") or it.get("message") or it.get("description") or ""
        dev = it.get("device_id") or ""
        seg = f"· {action}"
        if detail:
            seg += f"：{_truncate(str(detail), 120)}"
        if dev:
            seg += f"（设备 {dev}）"
        lines.append(seg)
    return "\n".join(lines)


# ============ 历史会话与资产 ============

@tool(
    "list_recent_conversations",
    description=(
        "列出 AI 中枢的历史会话（只读），按更新时间倒序。用于回答「我之前和你聊过什么」。"
        "参数：limit（默认 10，上限 30）。"
    ),
)
def list_recent_conversations(limit: int = 10) -> str:
    """列出历史会话摘要。"""
    owner = _execution_owner()
    if not owner:
        return "当前执行上下文没有用户归属，无法查询历史会话。"

    async def _load() -> list[dict[str, Any]]:
        from api.dao import ai_hub as ai_hub_dao
        from api.db.mongodb import get_db

        return await ai_hub_dao.list_conversations(
            get_db(),
            owner=owner,
            limit=max(1, min(int(limit or 10), 30)),
        )

    try:
        items = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"列出历史会话失败：{exc}"
    if not items:
        return "暂无历史会话。"

    lines = [f"返回 {len(items)} 个近期会话："]
    for idx, c in enumerate(items, 1):
        title = c.get("title") or "(新会话)"
        cnt = c.get("message_count") or 0
        lines.append(f"{idx}. {title}（{cnt} 条消息）")
    return "\n".join(lines)


@tool(
    "list_project_assets",
    description=(
        "列出某项目的资产测绘结果（只读，FOFA/Hunter 等）：host、IP、端口、根域名。"
        "用于了解目标的网络资产面。参数：project_id（必填）；root_domain（可选，按根域名过滤）；"
        "limit（默认 20，上限 50）。"
    ),
)
def list_project_assets(
    project_id: str,
    root_domain: str = "",
    limit: int = 20,
) -> str:
    """列出项目资产摘要。"""
    project_id = (project_id or "").strip()
    if not project_id:
        return "请提供 project_id。"

    async def _load() -> list[dict[str, Any]]:
        from api.dao import fofa_assets as fofa_dao
        from api.db.mongodb import get_db

        return await fofa_dao.query_assets(
            get_db(),
            project_id,
            root_domain=(root_domain or "").strip(),
            limit=max(1, min(int(limit or 20), 50)),
        )

    try:
        items = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"列出资产失败：{exc}"
    if not items:
        return f"项目 {project_id} 暂无资产记录。"

    lines = [f"项目 {project_id} 资产（返回 {len(items)} 条）："]
    for it in items[:limit]:
        host = it.get("host") or it.get("url") or ""
        ip = it.get("ip") or ""
        port = it.get("port") or ""
        rd = it.get("root_domain") or ""
        seg = f"· {host or ip}"
        extra = "／".join(str(x) for x in (ip if host else "", port, rd) if x)
        if extra:
            seg += f"（{extra}）"
        if rd:
            seg += f" {_refs.company_ref(rd, rd)}"
        lines.append(seg)
    return "\n".join(lines)


# ============ 社媒采集（小红书 / 抖音） ============

@tool(
    "list_xhs_notes",
    description=(
        "列出某项目的小红书笔记采集结果（只读）。用于了解该项目在小红书上命中的内容与可疑目标。"
        "参数：project_id（必填）；task_id（可选，按采集任务过滤）；"
        "is_suspicious（可选，true/false，仅看可疑/非可疑）；"
        "sort_by（可选，relevance 按关键词相关度+关注度，或 created_at 按时间，默认 relevance）；"
        "limit（默认 20，上限 50）。"
    ),
)
def list_xhs_notes(
    project_id: str,
    task_id: str = "",
    is_suspicious: str = "",
    sort_by: str = "relevance",
    limit: int = 20,
) -> str:
    """列出项目小红书笔记摘要。"""
    project_id = (project_id or "").strip()
    if not project_id:
        return "请提供 project_id。"

    susp: bool | None
    flag = (is_suspicious or "").strip().lower()
    if flag in ("true", "1", "yes"):
        susp = True
    elif flag in ("false", "0", "no"):
        susp = False
    else:
        susp = None

    async def _load() -> tuple[list[dict[str, Any]], int]:
        from api.dao import xhs as xhs_dao
        from api.db.mongodb import get_db

        return await xhs_dao.list_notes(
            get_db(),
            project_id=project_id,
            task_id=(task_id or "").strip() or None,
            is_suspicious=susp,
            limit=max(1, min(int(limit or 20), 50)),
            sort_by=(sort_by or "relevance").strip(),
        )

    try:
        items, total = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"列出小红书笔记失败：{exc}"
    if not items:
        return f"项目 {project_id} 暂无小红书笔记。"

    lines = [f"项目 {project_id} 小红书笔记（命中 {total} 条，返回前 {len(items)} 条）："]
    for idx, n in enumerate(items, 1):
        title = n.get("title") or "(无标题)"
        nick = (n.get("user") or {}).get("nickname") or ""
        tagging = n.get("tagging") or {}
        score = tagging.get("attention_score")
        seg = f"{idx}. {title}"
        if nick:
            seg += f"（作者：{nick}）"
        if score is not None:
            seg += f" 关注度 {score}"
        if tagging.get("is_suspicious"):
            seg += " ⚠可疑"
        lines.append(seg)
    return "\n".join(lines)


@tool(
    "list_xhs_note_details",
    description=(
        "列出某项目已抓取正文的小红书笔记详情（只读）：含正文内容、评论摘要、结构化研判。"
        "用于深入了解已进一步分析的笔记。参数：project_id（必填）；limit（默认 10，上限 30）。"
    ),
)
def list_xhs_note_details(
    project_id: str,
    limit: int = 10,
) -> str:
    """列出项目小红书笔记详情摘要。"""
    project_id = (project_id or "").strip()
    if not project_id:
        return "请提供 project_id。"

    async def _load() -> list[dict[str, Any]]:
        from api.dao import xhs as xhs_dao
        from api.db.mongodb import get_db

        return await xhs_dao.list_note_details(
            get_db(),
            project_id,
            limit=max(1, min(int(limit or 10), 30)),
        )

    try:
        items = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"列出小红书笔记详情失败：{exc}"
    if not items:
        return f"项目 {project_id} 暂无小红书笔记详情。"

    lines = [f"项目 {project_id} 小红书笔记详情（返回 {len(items)} 条）："]
    for idx, d in enumerate(items, 1):
        tagging = d.get("tagging") or {}
        summary = tagging.get("summary") or _truncate(d.get("content") or "", 120)
        seg = f"{idx}. 笔记 {d.get('note_id', '')}"
        if tagging.get("attention_score") is not None:
            seg += f" 关注度 {tagging.get('attention_score')}"
        if summary:
            seg += f"：{summary}"
        lines.append(seg)
    return "\n".join(lines)


@tool(
    "list_xhs_profiles",
    description=(
        "列出某项目的小红书用户画像（只读）：昵称、身份研判、公司识别、关注度、推荐动作。"
        "用于挖掘社媒侧的真实人物目标。参数：project_id（必填）；task_id（可选）；limit（默认 20，上限 50）。"
    ),
)
def list_xhs_profiles(
    project_id: str,
    task_id: str = "",
    limit: int = 20,
) -> str:
    """列出项目小红书用户画像摘要。"""
    project_id = (project_id or "").strip()
    if not project_id:
        return "请提供 project_id。"

    async def _load() -> tuple[list[dict[str, Any]], int]:
        from api.dao import xhs as xhs_dao
        from api.db.mongodb import get_db

        return await xhs_dao.list_profiles(
            get_db(),
            project_id,
            task_id=(task_id or "").strip() or None,
            limit=max(1, min(int(limit or 20), 50)),
        )

    try:
        items, total = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"列出小红书画像失败：{exc}"
    if not items:
        return f"项目 {project_id} 暂无小红书用户画像。"

    lines = [f"项目 {project_id} 小红书画像（命中 {total} 人，返回前 {len(items)} 人）："]
    for idx, p in enumerate(items, 1):
        nick = p.get("nickname") or "(未知昵称)"
        company = p.get("company_identification") or {}
        company_name = company.get("company_name") if isinstance(company, dict) else ""
        score = p.get("attention_score")
        fid = p.get("finding_id") or ""
        seg = f"{idx}. {nick}"
        if company_name:
            seg += f"（{company_name}）"
        if score is not None:
            seg += f" 关注度 {score}"
        if fid:
            seg += f" {_refs.finding_ref(str(fid), nick)}"
        lines.append(seg)
    return "\n".join(lines)


@tool(
    "list_douyin_search_results",
    description=(
        "列出某项目的抖音搜索采集结果（只读）：视频标题、作者、互动量、归属地。"
        "用于了解抖音侧命中的内容。参数：project_id（必填）；keyword（可选，按搜索关键词过滤）；"
        "limit（默认 20，上限 50）。"
    ),
)
def list_douyin_search_results(
    project_id: str,
    keyword: str = "",
    limit: int = 20,
) -> str:
    """列出项目抖音搜索结果摘要。"""
    project_id = (project_id or "").strip()
    if not project_id:
        return "请提供 project_id。"

    async def _load() -> tuple[list[dict[str, Any]], int]:
        from api.dao import douyin as douyin_dao
        from api.db.mongodb import get_db

        return await douyin_dao.list_search_results(
            get_db(),
            project_id,
            keyword=(keyword or "").strip() or None,
            limit=max(1, min(int(limit or 20), 50)),
        )

    try:
        items, total = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"列出抖音搜索结果失败：{exc}"
    if not items:
        return f"项目 {project_id} 暂无抖音搜索结果。"

    lines = [f"项目 {project_id} 抖音搜索结果（命中 {total} 条，返回前 {len(items)} 条）："]
    for idx, v in enumerate(items, 1):
        title = v.get("title") or "(无标题)"
        nick = v.get("nickname") or ""
        liked = v.get("liked_count")
        loc = v.get("ip_location") or ""
        seg = f"{idx}. {title}"
        if nick:
            seg += f"（作者：{nick}）"
        meta = "／".join(str(x) for x in (f"赞{liked}" if liked else "", loc) if x)
        if meta:
            seg += f" {meta}"
        lines.append(seg)
    return "\n".join(lines)


@tool(
    "list_douyin_profiles",
    description=(
        "列出某项目的抖音用户画像（只读）：昵称、公司识别、研判标签、优先级、关注度。"
        "用于挖掘抖音侧的真实人物目标。参数：project_id（必填）；limit（默认 20，上限 50）。"
    ),
)
def list_douyin_profiles(
    project_id: str,
    limit: int = 20,
) -> str:
    """列出项目抖音用户画像摘要。"""
    project_id = (project_id or "").strip()
    if not project_id:
        return "请提供 project_id。"

    async def _load() -> tuple[list[dict[str, Any]], int]:
        from api.dao import douyin as douyin_dao
        from api.db.mongodb import get_db

        return await douyin_dao.list_profiles(
            get_db(),
            project_id,
            limit=max(1, min(int(limit or 20), 50)),
        )

    try:
        items, total = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"列出抖音画像失败：{exc}"
    if not items:
        return f"项目 {project_id} 暂无抖音用户画像。"

    lines = [f"项目 {project_id} 抖音画像（命中 {total} 人，返回前 {len(items)} 人）："]
    for idx, p in enumerate(items, 1):
        nick = p.get("nickname") or "(未知昵称)"
        company = p.get("company_mentioned") or ""
        tag = p.get("tag") or ""
        score = p.get("attention_score")
        seg = f"{idx}. {nick}"
        if company:
            seg += f"（{company}）"
        extra = "／".join(str(x) for x in (tag, f"关注度{score}" if score is not None else "") if x)
        if extra:
            seg += f" {extra}"
        lines.append(seg)
    return "\n".join(lines)


# 供 AI 中枢复用的只读数据查询工具集
READ_TOOLS = [
    list_projects,
    get_project,
    list_task_logs,
    get_finding_detail,
    get_finding_copywriting,
    get_finding_profile,
    list_contact_profiles,
    get_contact_profile,
    list_mobile_operations,
    list_recent_conversations,
    list_project_assets,
    list_xhs_notes,
    list_xhs_note_details,
    list_xhs_profiles,
    list_douyin_search_results,
    list_douyin_profiles,
]
