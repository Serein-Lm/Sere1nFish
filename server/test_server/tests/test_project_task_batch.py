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
    from api.dao import targets as targets_dao
    from api.routers import project_api
    from api.services.info_collection import tuning as tuning_service
    from core import background as background_service

    captured_documents: list[dict] = []
    captured_coroutines = []

    async def get_project(_db, project_id):
        assert project_id == "project-1"
        return {"id": project_id}

    async def insert_tasks(_db, documents):
        captured_documents.extend(documents)
        return len(documents)

    async def list_project_targets(*_args, **_kwargs):
        return [
            {
                "target_id": "target-anhui",
                "target_name": "安徽广播电视台",
                "display_name": "安徽广播电视台",
                "batch_tags": ["第一批"],
            }
        ]

    class _Tuning:
        company_scan_concurrency = 2
        company_dispatch_concurrency = 8

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
    monkeypatch.setattr(targets_dao, "list_project_targets", list_project_targets)
    monkeypatch.setattr(background_service, "spawn_background", spawn)
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
    assert captured_documents[0]["params"]["target_id"] == "target-anhui"
    assert captured_documents[0]["params"]["refresh_target_identity"] is False
    assert captured_documents[1]["params"]["target_id"] == ""
    assert all(doc["batch_id"] == response["batch_id"] for doc in captured_documents)
    assert all(doc["batch_concurrency"] == 2 for doc in captured_documents)
    assert all(doc["batch_dispatch_concurrency"] == 2 for doc in captured_documents)
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


@pytest.mark.asyncio
async def test_company_scan_enqueue_bounds_admitted_runtime_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import company_scan_batch as batch_service
    from api.services import project_task_batch as project_batch
    from core import background as background_service

    documents: list[dict] = []
    scheduled: list[dict] = []
    spawned = []

    async def insert_tasks(_db, values):
        documents.extend(values)
        return len(values)

    def run_batch(**kwargs):
        scheduled.append(kwargs)

        async def idle():
            return None

        return idle()

    def spawn(coro, *, name=None):
        spawned.append((coro, name))

    monkeypatch.setattr(batch_service.tasks_dao, "insert_tasks", insert_tasks)
    monkeypatch.setattr(project_batch, "run_project_task_batch", run_batch)
    monkeypatch.setattr(background_service, "spawn_background", spawn)
    specs = [
        batch_service.CompanyScanJobSpec(
            target_id=f"target-{index}",
            company_name=f"公司 {index}",
            params={},
        )
        for index in range(100)
    ]

    response = await batch_service.enqueue_company_scan_jobs(
        object(),
        project_id="project-1",
        specs=specs,
        requested_by="admin",
        concurrency=6,
        dispatch_concurrency=24,
    )

    assert response["concurrency"] == 6
    assert response["dispatch_concurrency"] == 24
    assert len(documents) == 100
    assert all(item["batch_dispatch_concurrency"] == 24 for item in documents)
    assert scheduled[0]["concurrency"] == 6
    assert scheduled[0]["dispatch_concurrency"] == 24
    spawned[0][0].close()


@pytest.mark.asyncio
async def test_task_list_uses_lightweight_projection() -> None:
    from api.dao import tasks as tasks_dao

    captured: dict = {}

    class Cursor:
        def sort(self, *_args):
            return self

        def skip(self, *_args):
            return self

        def limit(self, *_args):
            return self

        async def to_list(self, _limit):
            return [{"task_id": "task-1", "params": {"company_name": "目标公司"}}]

    class Collection:
        async def count_documents(self, query):
            captured["count_query"] = query
            return 1

        def find(self, query, projection):
            captured["query"] = query
            captured["projection"] = projection
            return Cursor()

    class Database:
        def __getitem__(self, _name):
            return Collection()

    items, total = await tasks_dao.list_tasks(
        Database(),
        "project-1",
        task_type="company_scan",
        limit=20,
    )

    assert total == 1
    assert items[0]["task_id"] == "task-1"
    assert captured["query"] == {
        "project_id": "project-1",
        "task_type": "company_scan",
    }
    assert captured["projection"]["params.company_name"] == 1
    assert captured["projection"]["result.status"] == 1
    assert "result" not in captured["projection"]
    assert "checkpoint" not in captured["projection"]


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


def test_company_scan_validates_control_relation_depth() -> None:
    from api.routers.project_api import _validate_company_scan_params

    params = {"enable_control_structure": True, "control_max_depth": "2"}
    _validate_company_scan_params(params)
    assert params["control_max_depth"] == 2

    with pytest.raises(ValueError, match="必须为 1 或 2"):
        _validate_company_scan_params(
            {"enable_control_structure": True, "control_max_depth": 3}
        )


def test_company_scan_validates_website_collection_mode() -> None:
    from api.routers.project_api import _validate_company_scan_params

    params: dict = {}
    _validate_company_scan_params(params)
    assert params["website_collection_mode"] == "deep"

    params = {"website_collection_mode": " DEEP "}
    _validate_company_scan_params(params)
    assert params["website_collection_mode"] == "deep"

    with pytest.raises(ValueError, match="standard 或 deep"):
        _validate_company_scan_params({"website_collection_mode": "unbounded"})


def test_company_scan_validates_bidding_lookback_days() -> None:
    from api.routers.project_api import _validate_company_scan_params

    params = {"bidding_lookback_days": "7"}
    _validate_company_scan_params(params)
    assert params["bidding_lookback_days"] == 7

    with pytest.raises(ValueError, match="必须为 1 到 30"):
        _validate_company_scan_params({"bidding_lookback_days": 31})


def test_company_scan_normalizes_website_path_scope() -> None:
    from api.routers.project_api import _validate_company_scan_params

    params = {"website_required_path_segments": [" /AH/ ", "ah"]}

    _validate_company_scan_params(params)

    assert params["website_required_path_segments"] == ["ah"]


def test_company_scan_normalizes_official_website_roots() -> None:
    from api.routers.project_api import _validate_company_scan_params

    params = {
        "website_root_domains": [
            "https://www.express-sn.com/#1",
            "EXPRESS-SN.COM",
        ]
    }

    _validate_company_scan_params(params)

    assert params["website_root_domains"] == ["express-sn.com"]


def test_company_scan_rejects_invalid_official_website_root() -> None:
    from api.routers.project_api import _validate_company_scan_params

    with pytest.raises(ValueError, match="无效的官网根域名"):
        _validate_company_scan_params(
            {"website_root_domains": ["https://example.com:8443/"]}
        )


def test_company_scan_rejects_invalid_website_path_scope() -> None:
    from api.routers.project_api import _validate_company_scan_params

    with pytest.raises(ValueError, match="无效的官网路径段"):
        _validate_company_scan_params(
            {"website_required_path_segments": ["ah/other"]}
        )
