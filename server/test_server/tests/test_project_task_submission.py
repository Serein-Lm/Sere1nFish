from __future__ import annotations

from typing import Any

import pytest

from api.services.project_tasks import registry
from api.services.project_tasks import service


def test_project_task_registry_describes_registered_capabilities() -> None:
    definitions = registry.list_project_task_definitions()

    assert len(definitions) == 10
    assert len({item.task_type for item in definitions}) == len(definitions)
    assert registry.get_project_task_definition("company_scan").file_field == "url_text"
    assert registry.get_project_task_definition("company_scan").supports_skills is True
    assert registry.get_project_task_definition("mobile_collect").file_field is None


@pytest.mark.asyncio
async def test_submit_project_task_uses_one_persistence_and_runtime_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    runtime_marker = object()

    async def get_project(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"id": "project-1"}

    async def prepare(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        captured["prepare"] = kwargs
        return {**kwargs["params"], "normalized": True}

    async def insert_task(_db: Any, document: dict[str, Any]) -> dict[str, Any]:
        captured["document"] = document
        return document

    def execute(*args: Any) -> object:
        captured["execute"] = args
        return runtime_marker

    def spawn(awaitable: object, *, name: str) -> object:
        captured["spawn"] = (awaitable, name)
        return runtime_marker

    monkeypatch.setattr(service.projects_dao, "get_project", get_project)
    monkeypatch.setattr(service, "prepare_project_task_params", prepare)
    monkeypatch.setattr(service.tasks_dao, "insert_task", insert_task)
    monkeypatch.setattr(service, "execute_project_task", execute)
    monkeypatch.setattr(service, "spawn_background", spawn)

    submitted = {"company_name": "示例公司"}
    result = await service.submit_project_task(
        object(),
        project_id="project-1",
        task_type="company_scan",
        params=submitted,
        requested_by="admin",
        file_text="https://example.com",
    )

    assert submitted == {"company_name": "示例公司"}
    assert captured["prepare"]["params"]["url_text"] == "https://example.com"
    assert captured["document"]["params"]["normalized"] is True
    assert captured["execute"][1:] == (
        "project-1",
        "company_scan",
        {
            "company_name": "示例公司",
            "url_text": "https://example.com",
            "normalized": True,
            "_requested_by": "admin",
        },
    )
    assert captured["spawn"] == (runtime_marker, f"task:{result['task_id']}")
    assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_submit_project_task_rejects_file_for_unsupported_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def get_project(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"id": "project-1"}

    monkeypatch.setattr(service.projects_dao, "get_project", get_project)

    with pytest.raises(service.UnsupportedProjectTaskError, match="不支持文件输入"):
        await service.submit_project_task(
            object(),
            project_id="project-1",
            task_type="mobile_collect",
            params={},
            requested_by="admin",
            file_text="payload",
        )
