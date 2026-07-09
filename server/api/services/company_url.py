from __future__ import annotations

import re


def normalize_url(url: str) -> str:
    s = (url or "").strip()
    if not s:
        return s
    if not re.match(r"^https?://", s, flags=re.IGNORECASE):
        s = "https://" + s
    return s


def guess_url_from_company_name(name: str) -> str | None:
    """轻量规则：
    - 输入本身是 URL/域名则直接规范化返回
    - 否则返回 None（后续可接天眼查/LLM）
    """
    s = (name or "").strip()
    if not s:
        return None

    # 直接就是 URL
    if re.match(r"^https?://", s, flags=re.IGNORECASE):
        return normalize_url(s)

    # 简单域名形态
    if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", s, flags=re.IGNORECASE):
        return normalize_url(s)

    return None
