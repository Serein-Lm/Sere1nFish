"""Declarative registry for project task capabilities."""
from __future__ import annotations

from dataclasses import dataclass

from api.services.project_task_runtime import (
    TaskDispatcher,
    register_task_dispatchers,
)
from api.services.project_tasks import dispatchers


@dataclass(frozen=True, slots=True)
class ProjectTaskDefinition:
    task_type: str
    label: str
    dispatcher: TaskDispatcher
    file_field: str | None = None
    supports_skills: bool = False

    def public_view(self) -> dict[str, object]:
        return {
            "task_type": self.task_type,
            "label": self.label,
            "supports_file": self.file_field is not None,
            "supports_skills": self.supports_skills,
        }


_DEFINITIONS = (
    ProjectTaskDefinition(
        "url_scan",
        "URL 扫描",
        dispatchers.dispatch_url_scan,
        file_field="url_text",
        supports_skills=True,
    ),
    ProjectTaskDefinition(
        "xhs_search",
        "小红书搜索",
        dispatchers.dispatch_xhs_search,
    ),
    ProjectTaskDefinition(
        "douyin_search",
        "抖音搜索",
        dispatchers.dispatch_douyin_search,
    ),
    ProjectTaskDefinition(
        "web_tagging",
        "网站标注",
        dispatchers.dispatch_web_tagging,
    ),
    ProjectTaskDefinition(
        "company_scan",
        "综合公司扫描",
        dispatchers.dispatch_company_scan,
        file_field="url_text",
        supports_skills=True,
    ),
    ProjectTaskDefinition(
        "fofa_collect",
        "资产发现",
        dispatchers.dispatch_fofa_collect,
        supports_skills=True,
    ),
    ProjectTaskDefinition(
        "scholar_contact",
        "学者联系采集",
        dispatchers.dispatch_scholar_contact,
    ),
    ProjectTaskDefinition(
        "mobile_collect",
        "手机采集",
        dispatchers.dispatch_mobile_collect,
    ),
    ProjectTaskDefinition(
        "target_research",
        "Target 深研",
        dispatchers.dispatch_target_research,
    ),
    ProjectTaskDefinition(
        "social_media_collect",
        "社交媒体采集",
        dispatchers.dispatch_social_media_collect,
    ),
)
_BY_TYPE = {definition.task_type: definition for definition in _DEFINITIONS}


def get_project_task_definition(task_type: str) -> ProjectTaskDefinition | None:
    return _BY_TYPE.get(str(task_type or "").strip())


def list_project_task_definitions() -> tuple[ProjectTaskDefinition, ...]:
    return _DEFINITIONS


def register_default_project_task_dispatchers() -> None:
    register_task_dispatchers(
        {
            definition.task_type: definition.dispatcher
            for definition in _DEFINITIONS
        }
    )
