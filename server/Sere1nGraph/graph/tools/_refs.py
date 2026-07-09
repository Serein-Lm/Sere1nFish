"""
可跳转引用标记 — 供 AI 中枢工具在返回文本中内嵌实体引用。

统一标记格式（前端解析后渲染为可点击 chip，跳转到人设库/项目详情等）：

    [[ref:person:{person_id}|{label}]]
    [[ref:finding:{finding_id}|{label}]]
    [[ref:company:{root_domain}|{label}]]
    [[ref:project:{project_id}|{label}]]

设计原则：标记只承载"跳转到哪个实体"的语义，不含平台协议或页面路由细节；
路由映射收敛在前端。工具侧只调用本 helper 生成稳定标记。
"""
from __future__ import annotations

_ALLOWED_TYPES = {"person", "finding", "company", "project"}


def ref_tag(entity_type: str, entity_id: str, label: str = "") -> str:
    """生成单个可跳转引用标记。id 为空时返回空串（不产出无效跳转）。"""
    entity_type = (entity_type or "").strip()
    entity_id = (entity_id or "").strip()
    if entity_type not in _ALLOWED_TYPES or not entity_id:
        return ""
    text = (label or entity_id).replace("|", "／").replace("]", "）")
    return f"[[ref:{entity_type}:{entity_id}|{text}]]"


def person_ref(person_id: str, label: str = "") -> str:
    return ref_tag("person", person_id, label)


def finding_ref(finding_id: str, label: str = "") -> str:
    return ref_tag("finding", finding_id, label)


def company_ref(root_domain: str, label: str = "") -> str:
    return ref_tag("company", root_domain, label)


def project_ref(project_id: str, label: str = "") -> str:
    return ref_tag("project", project_id, label)
