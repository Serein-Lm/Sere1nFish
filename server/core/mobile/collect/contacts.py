"""采集联系方式 & 背景抽取 — 从结构化记录文本中识别联系方式并组装 findings。

设计:persist 阶段调用 extract_contacts 从记录可搜索文本里正则抽取手机号/座机/
邮箱/微信号/QQ,再由 build_contact_findings 组装成统一 findings dict(source=mobile,
type=contact),交给 findings_dao.upsert_contact_finding 幂等入库。背景信息作为 finding
的 context/evidence 落库,前端 findings 面板即可看到并联系。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

# ── 正则 ────────────────────────────────────────────────

# 手机号:11 位,1 开头,第二位 3-9;两侧非数字边界避免截取长串中段
_RE_MOBILE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
# 座机:区号(3-4位,可选 0 前缀)+ 分隔 + 7-8 位;要求带分隔符降低误报
_RE_TEL = re.compile(r"(?<!\d)(0\d{2,3}[-\s]\d{7,8})(?!\d)")
# 关键词锚定电话:电话/联系电话/联系方式/tel 后接 7-12 位数字(可含区号分隔),
# 用于捕捉无区号的本地座机(如「电话：58158252」)等 _RE_TEL 漏掉的情况
_RE_TEL_KW = re.compile(
    r"(?:联系电话|电话|联系方式|Tel|TEL|tel)\s*[:：]?\s*(\d{11}|(?:0\d{2,3}[-\s]?)?\d{7,8}(?!\d))",
)
_RE_MOBILE_KW = re.compile(
    r"(?:手机|手机号码?|联系人电话|移动电话|mobile)\s*[:：]?\s*(1[3-9]\d{9})",
    re.IGNORECASE,
)
# 邮箱
_RE_EMAIL = re.compile(r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
# 微信号:关键词后接 6-20 位字母数字下划线减号(需含字母,纯数字易与手机号混)
_RE_WECHAT = re.compile(
    r"(?:微信号?|微信|weixin|wechat|vx|wx)\s*[:：]?\s*([A-Za-z][A-Za-z0-9_\-]{5,19})",
    re.IGNORECASE,
)
# QQ:关键词后接 5-12 位数字
_RE_QQ = re.compile(r"(?:QQ|qq)\s*[:：]?\s*(\d{5,12})", re.IGNORECASE)

# channel 中文标签
_CHANNEL_LABEL = {
    "phone": "手机号",
    "telephone": "座机",
    "email": "邮箱",
    "wechat": "微信号",
    "qq": "QQ",
}


def _clean_tel(value: str) -> str:
    return re.sub(r"\s+", "", value)


def extract_contacts(text_blob: str) -> list[dict[str, str]]:
    """从一段文本中抽取分类联系方式,去重后返回。

    返回 [{"channel","value","label"}],channel ∈ phone/telephone/email/wechat/qq。
    """
    if not text_blob:
        return []
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(channel: str, value: str) -> None:
        value = value.strip()
        if not value:
            return
        key = (channel, value.lower())
        if key in seen:
            return
        seen.add(key)
        found.append(
            {
                "channel": channel,
                "value": value,
                "label": f"{_CHANNEL_LABEL.get(channel, channel)}: {value}",
            }
        )

    for m in _RE_EMAIL.finditer(text_blob):
        _add("email", m.group(1))
    for m in _RE_MOBILE.finditer(text_blob):
        _add("phone", m.group(1))
    for m in _RE_MOBILE_KW.finditer(text_blob):
        _add("phone", m.group(1))
    for m in _RE_TEL.finditer(text_blob):
        _add("telephone", _clean_tel(m.group(1)))
    for m in _RE_TEL_KW.finditer(text_blob):
        digits = _clean_tel(m.group(1))
        if len(digits) == 11 and digits[0] == "1":
            _add("phone", digits)
        else:
            _add("telephone", digits)
    for m in _RE_WECHAT.finditer(text_blob):
        _add("wechat", m.group(1))
    for m in _RE_QQ.finditer(text_blob):
        _add("qq", m.group(1))
    return found


# 有无联系方式决定分级基准:有联系方式 → 高分带(>=80);无 → 低分带(<40)
_CONTACT_FLOOR = 80
_NO_CONTACT_CEIL = 39


def grade_with_contacts(base_score: Any, has_contacts: bool) -> int:
    """按「有联系方式才能高、无联系方式一定低」规则给记录定级。

    base_score 为 LLM 相关性分,仅作带内微调。有联系方式抬到高分带(>=80),
    无联系方式压到低分带(<40)。返回 0-100 的整数。
    """
    try:
        base = int(base_score)
    except (TypeError, ValueError):
        base = 0
    base = max(0, min(100, base))
    if has_contacts:
        return max(base, _CONTACT_FLOOR)
    return min(base, _NO_CONTACT_CEIL)


def record_text_blob(fields: dict[str, Any], source_url: str | None = None) -> str:
    """把记录字段值(+链接)拼成一段可供正则搜索的文本。"""
    parts: list[str] = []
    for v in (fields or {}).values():
        if isinstance(v, (list, tuple)):
            parts.extend(str(x) for x in v)
        elif v not in (None, ""):
            parts.append(str(v))
    if source_url:
        parts.append(source_url)
    return "\n".join(parts)


def _contact_finding_id(project_id: str, channel: str, value: str) -> str:
    raw = f"mobile_contact:{project_id}:{channel}:{value}".encode("utf-8")
    return "mc_" + hashlib.sha1(raw).hexdigest()[:20]


def _build_context(fields: dict[str, Any]) -> str:
    """从记录字段拼出一段中文背景说明(标题/来源/摘要/背景 优先)。"""
    ordered_keys = ["title", "source", "publish_time", "background", "summary"]
    parts: list[str] = []
    for k in ordered_keys:
        v = fields.get(k)
        if v not in (None, "", [], {}):
            parts.append(f"{k}: {v}")
    for k, v in fields.items():
        if k in ordered_keys or k in ("contact",):
            continue
        if v not in (None, "", [], {}):
            parts.append(f"{k}: {v}")
    return "；".join(parts)


def build_contact_findings(
    *,
    project_id: str,
    task_id: str,
    record: dict[str, Any],
    contacts: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """把抽到的联系方式组装成统一 findings dict 列表(未落库)。

    record 为统一记录形状 {"fields","score","source_url",...}。联系方式继承条目
    relevance 分作为 attention_score,背景信息进 context/evidence。
    """
    if not project_id or not contacts:
        return []
    fields = record.get("fields") or {}
    score = record.get("score")
    attention = int(score) if isinstance(score, int) else 60
    source_url = record.get("source_url")
    context = _build_context(fields)
    title = str(fields.get("title") or fields.get("summary") or "").strip()
    record_id = record.get("record_id", "")
    keyword = record.get("keyword", "")
    screenshot_url = record.get("screenshot_url")

    out: list[dict[str, Any]] = []
    for c in contacts:
        channel = c["channel"]
        value = c["value"]
        finding_id = _contact_finding_id(project_id, channel, value)
        out.append(
            {
                "finding_id": finding_id,
                "project_id": project_id,
                "task_id": task_id,
                "source": "mobile",
                "type": "contact",
                "channel": channel,
                "label": c["label"],
                "value": value,
                "url": source_url or "",
                "attention_score": attention,
                "attention_reason": title or context[:80],
                "context": context,
                "has_profile": False,
                "evidence": {
                    "record_id": record_id,
                    "keyword": keyword,
                    "screenshot_url": screenshot_url,
                    "source_url": source_url,
                    "fields": fields,
                },
            }
        )
    return out
