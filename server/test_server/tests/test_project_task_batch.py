from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from api.services.project_task_batch import (
    ProjectTaskJob,
    parse_company_names,
    run_project_task_batch,
)


def test_parse_company_names_preserves_order_and_removes_duplicates() -> None:
    assert parse_company_names(
        " 安徽广播电视台\n\n鞍钢集团有限公司\n安徽广播电视台 "
    ) == ["安徽广播电视台", "鞍钢集团有限公司"]


def test_project_task_batch_uses_bounded_concurrency(monkeypatch) -> None:
    import api.services.project_task_batch as batch_service

    active = 0
    peak = 0
    completed: list[str] = []

    async def executor(task_id, _project_id, _task_type, _params):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        completed.append(task_id)
        active -= 1

    monkeypatch.setattr(batch_service, "obs_log", lambda *args, **kwargs: None)
    jobs = [
        ProjectTaskJob(
            task_id=f"task-{index}",
            project_id="project-1",
            task_type="company_scan",
            params={"company_name": f"公司 {index}"},
        )
        for index in range(5)
    ]

    asyncio.run(
        run_project_task_batch(
            batch_id="batch-1",
            project_id="project-1",
            jobs=jobs,
            executor=executor,
            concurrency=2,
        )
    )

    assert peak == 2
    assert sorted(completed) == [f"task-{index}" for index in range(5)]


def test_project_task_batch_emits_one_aggregate_completion(monkeypatch) -> None:
    import api.services.project_task_batch as batch_service

    notifications: list[tuple[str, str]] = []

    async def executor(_task_id, _project_id, _task_type, _params):
        return None

    async def notify(*, batch_id: str, project_id: str) -> None:
        notifications.append((batch_id, project_id))

    monkeypatch.setattr(batch_service, "obs_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(batch_service, "_notify_company_batch_completion", notify)
    jobs = [
        ProjectTaskJob(
            task_id=f"task-{index}",
            project_id="project-1",
            task_type="company_scan",
            params={"company_name": f"公司 {index}"},
        )
        for index in range(3)
    ]

    asyncio.run(
        run_project_task_batch(
            batch_id="batch-1",
            project_id="project-1",
            jobs=jobs,
            executor=executor,
            concurrency=2,
            aggregate_notification=True,
        )
    )

    assert notifications == [("batch-1", "project-1")]


def test_company_scan_batch_api_creates_independent_task_documents(monkeypatch) -> None:
    from api.auth import User
    from api.routers import project_api
    from api.services.info_collection import tuning as tuning_service

    captured_documents: list[dict] = []
    captured_coroutines = []

    async def get_project(_db, project_id):
        assert project_id == "project-1"
        return {"id": project_id}

    async def insert_tasks(_db, documents):
        captured_documents.extend(documents)
        return len(documents)

    class _Tuning:
        company_scan_concurrency = 2

        def with_overrides(self, **overrides):
            assert overrides == {"company_scan_concurrency": 2}
            return self

    async def get_tuning():
        return _Tuning()

    def spawn(coro, *, name=None):
        captured_coroutines.append((coro, name))

    monkeypatch.setattr(project_api, "get_db", lambda: object())
    monkeypatch.setattr(project_api.projects_dao, "get_project", get_project)
    monkeypatch.setattr(project_api.tasks_dao, "insert_tasks", insert_tasks)
    monkeypatch.setattr(project_api, "spawn_background", spawn)
    monkeypatch.setattr(tuning_service, "get_collection_runtime_tuning", get_tuning)

    response = asyncio.run(
        project_api.create_company_scan_batch(
            "project-1",
            project_api.CompanyScanBatchCreateRequest(
                company_names=["安徽广播电视台", "鞍钢集团有限公司", "安徽广播电视台"],
                params={
                    "enable_xhs": False,
                    "enable_wechat": False,
                    "company_scan_concurrency": 2,
                },
            ),
            current_user=User(username="admin"),
        )
    )

    assert response["task_count"] == 2
    assert response["concurrency"] == 2
    assert [doc["params"]["company_name"] for doc in captured_documents] == [
        "安徽广播电视台",
        "鞍钢集团有限公司",
    ]
    assert all(doc["batch_id"] == response["batch_id"] for doc in captured_documents)
    assert all(doc["batch_concurrency"] == 2 for doc in captured_documents)
    assert [doc["batch_index"] for doc in captured_documents] == [1, 2]
    assert all("company_scan_concurrency" not in doc["params"] for doc in captured_documents)
    assert captured_coroutines[0][1] == f"task-batch:{response['batch_id']}"
    captured_coroutines[0][0].close()


def test_company_scan_batch_api_rejects_shared_urls(monkeypatch) -> None:
    from api.auth import User
    from api.routers import project_api

    async def get_project(_db, _project_id):
        return {"id": "project-1"}

    monkeypatch.setattr(project_api, "get_db", lambda: object())
    monkeypatch.setattr(project_api.projects_dao, "get_project", get_project)

    with pytest.raises(HTTPException, match="不能共用 URL"):
        asyncio.run(
            project_api.create_company_scan_batch(
                "project-1",
                project_api.CompanyScanBatchCreateRequest(
                    company_names=["公司一", "公司二"],
                    params={"urls": ["https://example.com"]},
                ),
                current_user=User(username="admin"),
            )
        )


def test_company_scan_allows_automatic_scholar_direction() -> None:
    from api.routers.project_api import _validate_company_scan_params

    params = {"enable_scholar": True}
    _validate_company_scan_params(params)
    assert "scholar_direction" not in params

    params = {"enable_scholar": True, "scholar_direction": "  金融科技  "}
    _validate_company_scan_params(params)
    assert params["scholar_direction"] == "金融科技"


def test_company_scan_validates_wechat_target_selection_mode() -> None:
    from api.routers.project_api import _validate_company_scan_params

    params = {"enable_wechat": True}
    _validate_company_scan_params(params)
    assert params["wechat_target_selection_mode"] == "auto"

    with pytest.raises(ValueError, match="auto 或 all"):
        _validate_company_scan_params(
            {
                "enable_wechat": True,
                "wechat_target_selection_mode": "manual",
            }
        )
