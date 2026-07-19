from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import pytest


@pytest.mark.asyncio
async def test_recovered_company_batch_dispatches_all_waiters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import task_runtime_recovery as recovery
    from api.services.info_collection import tuning as tuning_service

    scheduled: list[dict[str, Any]] = []
    background = []

    class _Tuning:
        company_scan_concurrency = 3

    async def _tuning() -> _Tuning:
        return _Tuning()

    def _batch(**kwargs: Any):
        scheduled.append(kwargs)

        async def _idle() -> None:
            return None

        return _idle()

    def _spawn(coro, *, name=None):
        background.append((coro, name))

    monkeypatch.setattr(tuning_service, "get_collection_runtime_tuning", _tuning)
    monkeypatch.setattr(recovery, "run_project_task_batch", _batch)
    monkeypatch.setattr(recovery, "spawn_background", _spawn)

    documents = [
        {
            "task_id": f"task-{index}",
            "project_id": "project-1",
            "task_type": "company_scan",
            "batch_id": "batch-1",
            "batch_index": index,
            "batch_total": 5,
            "params": {"company_name": f"公司 {index}"},
        }
        for index in range(1, 6)
    ]
    assert await recovery._schedule_recovered_tasks(documents) == 5
    assert scheduled[0]["concurrency"] == 3
    assert scheduled[0]["dispatch_concurrency"] == 5
    assert scheduled[0]["aggregate_notification"] is True
    assert [job.task_id for job in scheduled[0]["jobs"]] == [
        f"task-{index}" for index in range(1, 6)
    ]
    assert all(job.params["_batch_id"] == "batch-1" for job in scheduled[0]["jobs"])
    for coro, _name in background:
        coro.close()


@pytest.mark.asyncio
async def test_company_core_slot_can_be_reused_during_mobile_wait() -> None:
    from api.services.company_scan_runtime import CompanyScanResourcePool

    pool = CompanyScanResourcePool(1)
    first = pool.lease(task_id="first")
    second = pool.lease(task_id="second")
    await first.acquire()

    waiter = asyncio.create_task(second.acquire())
    await asyncio.sleep(0)
    assert not waiter.done()

    first.release()
    await asyncio.wait_for(waiter, timeout=0.2)
    assert second.acquired is True
    second.release()
    assert pool.active == 0


@pytest.mark.asyncio
async def test_stale_wechat_browser_lease_triggers_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from browser_manager.provider import ChromeDockerConfig, ContainerInfo, DockerProvider

    provider = DockerProvider(
        ChromeDockerConfig(wechat_article_lease_timeout=60)
    )
    info = ContainerInfo(
        container_id="container-1",
        container_name="chrome-test",
        cdp_host="chrome-test",
        cdp_port=9222,
        api_port=8250,
        vnc_port=5900,
        novnc_port=6080,
        status="busy",
        task_id="wechat-article-task-1",
        purpose="wechat_article",
        last_used_at=datetime.now() - timedelta(seconds=90),
    )
    reasons: list[str] = []

    async def _memory(_container_id: str) -> float:
        return 200.0

    async def _health(_info: ContainerInfo) -> tuple[bool, str]:
        return True, ""

    async def _recover(_info: ContainerInfo, *, reason: str) -> bool:
        reasons.append(reason)
        return True

    monkeypatch.setattr(provider, "_query_container_memory", _memory)
    monkeypatch.setattr(provider, "_query_cdp_health", _health)
    monkeypatch.setattr(provider, "_restart_chrome_for_recovery", _recover)

    await provider._inspect_container_health("container-1", info)
    assert reasons and "超过上限" in reasons[0]


@pytest.mark.asyncio
async def test_browser_recovery_falls_back_when_control_restart_does_not_restore_cdp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import notifications
    from browser_manager import provider as provider_module
    from browser_manager.provider import ChromeDockerConfig, ContainerInfo, DockerProvider

    provider = DockerProvider(ChromeDockerConfig())
    info = ContainerInfo(
        container_id="container-1",
        container_name="chrome-test",
        cdp_host="chrome-test",
        cdp_port=9222,
        api_port=8250,
        vnc_port=5900,
        novnc_port=6080,
        status="busy",
        task_id="task-1",
        purpose="wechat_article",
    )
    wait_timeouts: list[float] = []
    docker_restarts: list[str] = []

    class _Response:
        status_code = 200

    class _HttpClient:
        async def __aenter__(self) -> "_HttpClient":
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> _Response:
            return _Response()

    class _Container:
        def restart(self, *, timeout: int) -> None:
            docker_restarts.append(str(timeout))

    class _Containers:
        def get(self, _container_id: str) -> _Container:
            return _Container()

    class _Docker:
        containers = _Containers()

    async def _wait(_info: ContainerInfo, *, timeout: float) -> bool:
        wait_timeouts.append(timeout)
        return len(wait_timeouts) > 1

    monkeypatch.setattr(provider_module.httpx, "AsyncClient", _HttpClient)
    monkeypatch.setattr(provider, "_wait_cdp_recovered", _wait)
    monkeypatch.setattr(provider, "_get_docker_client", lambda: _Docker())
    monkeypatch.setattr(notifications, "notify_event_background", lambda **_kwargs: True)

    assert await provider._restart_chrome_for_recovery(
        info,
        reason="CDP 健康检查失败",
    ) is True
    assert wait_timeouts == [20, 30]
    assert docker_restarts == ["5"]
    assert info.cdp_healthy is True


def test_cdp_unhealthy_container_is_not_assignable() -> None:
    from browser_manager.provider import ContainerInfo, DockerProvider

    info = ContainerInfo(
        container_id="container-1",
        container_name="chrome-test",
        cdp_host="chrome-test",
        cdp_port=9222,
        api_port=8250,
        vnc_port=5900,
        novnc_port=6080,
        status="idle",
        cdp_healthy=False,
    )
    assert DockerProvider._is_assignable(info) is False


@pytest.mark.asyncio
async def test_wechat_capture_timeout_recovers_and_releases_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services.source_documents import wechat as module
    from api.services.source_documents.contracts import SourceDocumentError

    events: list[str] = []

    class _Provider:
        async def get_cdp_endpoint(self, **_kwargs: Any) -> str:
            return "ws://chrome/cdp-proxy"

        async def recover_task_container(self, **_kwargs: Any) -> bool:
            events.append("recover")
            return True

        async def report_error(self, **_kwargs: Any) -> None:
            return None

        async def release_cdp_endpoint(self, **_kwargs: Any) -> None:
            events.append("release")

    class _PlaywrightStarter:
        async def start(self) -> None:
            await asyncio.sleep(1)

    monkeypatch.setattr(module, "get_browser_provider", lambda: _Provider())
    monkeypatch.setattr(module, "async_playwright", lambda: _PlaywrightStarter())
    provider = module.WechatArticleProvider(max_attempts=1)
    provider.attempt_timeout_seconds = 0.01

    with pytest.raises(SourceDocumentError, match="整次浏览器读取超时"):
        await provider._capture_once(
            "https://mp.weixin.qq.com/s/example",
            requested_url="https://mp.weixin.qq.com/s/example",
            task_id="task-1",
        )
    assert events == ["recover", "release"]


def test_dingtalk_webhook_url_can_be_saved_without_stream_credentials() -> None:
    from api.services.dingtalk_configuration import normalize_webhook_access_token

    assert normalize_webhook_access_token(
        "https://oapi.dingtalk.com/robot/send?access_token=token-value"
    ) == "token-value"
    assert normalize_webhook_access_token("token-value") == "token-value"
    with pytest.raises(ValueError, match="格式不正确"):
        normalize_webhook_access_token(
            "https://example.com/robot/send?access_token=token-value"
        )


def test_scholar_institution_match_rejects_unrelated_candidate() -> None:
    from crawler_tools.scholar_tools import (
        _institution_candidate_matches,
        _institution_matches,
    )

    assert _institution_matches("鞍钢集团有限公司", "鞍钢集团有限公司") is True
    assert _institution_matches("安徽广播电视台", "安徽大学") is False
    assert _institution_matches(
        "中国银联股份有限公司",
        "China UnionPay",
        "China UnionPay Co., Ltd.",
    ) is True
    assert _institution_candidate_matches(
        "中国科学院",
        {
            "name": "Chinese Academy of Sciences",
            "aliases": ["Chinese Academy of Sciences", "中国科学院"],
        },
    ) is True


@pytest.mark.asyncio
async def test_scholar_article_verification_is_not_downgraded() -> None:
    from api.dao import scholar_contact as scholar_dao

    class _Result:
        def __init__(self, *, inserted: bool) -> None:
            self.upserted_id = "inserted" if inserted else None
            self.modified_count = 0 if inserted else 1

    class _Collection:
        def __init__(self) -> None:
            self.document: dict[str, Any] = {}

        async def update_one(
            self,
            _query: dict[str, Any],
            update: dict[str, Any],
            *,
            upsert: bool,
        ) -> _Result:
            assert upsert is True
            operator_keys = [set(values) for values in update.values() if isinstance(values, dict)]
            for index, keys in enumerate(operator_keys):
                assert all(not keys.intersection(other) for other in operator_keys[index + 1 :])
            inserted = not self.document
            if inserted:
                self.document.update(update.get("$setOnInsert", {}))
            self.document.update(update.get("$set", {}))
            return _Result(inserted=inserted)

    class _Db:
        def __init__(self) -> None:
            self.collection = _Collection()

        def __getitem__(self, _name: str) -> _Collection:
            return self.collection

    db = _Db()
    base = {
        "article_id": "article-1",
        "title": "Article",
        "unit": "中国科学院",
        "source_keys": ["openalex"],
    }
    await scholar_dao.upsert_articles_batch(
        db,
        project_id="project-1",
        unit="中国科学院",
        direction="人工智能",
        articles=[
            {
                **base,
                "unit_verified": True,
                "match_evidence": "OpenAlex institution=Chinese Academy of Sciences",
            }
        ],
    )
    await scholar_dao.upsert_articles_batch(
        db,
        project_id="project-1",
        unit="中国科学院",
        direction="人工智能",
        articles=[{**base, "unit_verified": False, "match_evidence": ""}],
    )

    assert db.collection.document["unit_verified"] is True
    assert db.collection.document["match_evidence"].startswith("OpenAlex institution=")


@pytest.mark.asyncio
async def test_pending_tasks_do_not_consume_recovery_budget() -> None:
    from api.dao import tasks as tasks_dao

    class _Cursor:
        def __init__(self, items: list[dict[str, Any]]) -> None:
            self.items = items

        async def to_list(self, _limit: int | None) -> list[dict[str, Any]]:
            return [dict(item) for item in self.items]

    class _Result:
        def __init__(self, modified_count: int) -> None:
            self.modified_count = modified_count

    class _Collection:
        def __init__(self, documents: list[dict[str, Any]]) -> None:
            self.documents = documents

        def find(self, query: dict[str, Any], _projection: dict[str, Any]) -> _Cursor:
            statuses = set(query["status"]["$in"])
            return _Cursor(
                [item for item in self.documents if item.get("status") in statuses]
            )

        async def update_many(
            self,
            query: dict[str, Any],
            update: dict[str, Any],
        ) -> _Result:
            task_ids = set(query["task_id"]["$in"])
            modified = 0
            for item in self.documents:
                if item.get("task_id") not in task_ids:
                    continue
                if query.get("status") and item.get("status") != query["status"]:
                    continue
                item.update(update.get("$set", {}))
                for key, value in update.get("$inc", {}).items():
                    item[key] = int(item.get(key) or 0) + int(value)
                for key in update.get("$unset", {}):
                    item.pop(key, None)
                modified += 1
            return _Result(modified)

    class _Db:
        def __init__(self, documents: list[dict[str, Any]]) -> None:
            self.collection = _Collection(documents)

        def __getitem__(self, _name: str) -> _Collection:
            return self.collection

    documents = [
        {"task_id": "pending", "status": "pending", "recovery_count": 3},
        {"task_id": "running", "status": "running", "recovery_count": 2},
        {
            "task_id": "waiting",
            "status": "running",
            "recovery_count": 3,
            "progress": {"stage": "waiting_core"},
        },
        {"task_id": "exhausted", "status": "running", "recovery_count": 3},
    ]
    recovered, exhausted = await tasks_dao.prepare_interrupted_tasks(_Db(documents))

    assert {item["task_id"] for item in recovered} == {
        "pending",
        "running",
        "waiting",
    }
    assert exhausted == 1
    assert documents[0]["status"] == "pending"
    assert documents[0]["recovery_count"] == 3
    assert documents[1]["status"] == "pending"
    assert documents[1]["recovery_count"] == 3
    assert documents[2]["status"] == "pending"
    assert documents[2]["recovery_count"] == 3
    assert documents[3]["status"] == "error"


@pytest.mark.asyncio
async def test_background_shutdown_cancels_and_drains_retained_tasks() -> None:
    from core.background import cancel_background_tasks, spawn_background

    started = asyncio.Event()
    stopped = asyncio.Event()

    async def worker() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    spawn_background(worker(), name="shutdown-test")
    await started.wait()

    assert await cancel_background_tasks(timeout=1) >= 1
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_batch_notification_claim_is_terminal_and_idempotent() -> None:
    from api.dao import tasks as tasks_dao

    class _Cursor:
        def __init__(self, items: list[dict[str, Any]]) -> None:
            self.items = items

        def sort(self, key: str, direction: int) -> "_Cursor":
            self.items.sort(key=lambda item: item.get(key, 0), reverse=direction < 0)
            return self

        async def to_list(self, _limit: int | None) -> list[dict[str, Any]]:
            return [dict(item) for item in self.items]

    class _Result:
        modified_count = 1

    class _Collection:
        def __init__(self, documents: list[dict[str, Any]]) -> None:
            self.documents = documents

        def find(self, query: dict[str, Any], _projection: dict[str, Any]) -> _Cursor:
            return _Cursor(
                [item for item in self.documents if item.get("batch_id") == query["batch_id"]]
            )

        async def find_one_and_update(
            self,
            query: dict[str, Any],
            update: dict[str, Any],
            **_kwargs: Any,
        ) -> dict[str, Any] | None:
            for item in self.documents:
                if item.get("task_id") != query.get("task_id"):
                    continue
                if item.get("batch_notification_sent_at") is not None:
                    return None
                if item.get("batch_notification_claimed_at") is not None:
                    return None
                item.update(update.get("$set", {}))
                return dict(item)
            return None

        async def update_one(
            self,
            query: dict[str, Any],
            update: dict[str, Any],
        ) -> _Result:
            for item in self.documents:
                if item.get("task_id") != query.get("task_id"):
                    continue
                if item.get("batch_notification_claim_token") != query.get(
                    "batch_notification_claim_token"
                ):
                    continue
                item.update(update.get("$set", {}))
                for key in update.get("$unset", {}):
                    item.pop(key, None)
                return _Result()

            class _NoChange:
                modified_count = 0

            return _NoChange()

    class _Db:
        def __init__(self, documents: list[dict[str, Any]]) -> None:
            self.collection = _Collection(documents)

        def __getitem__(self, _name: str) -> _Collection:
            return self.collection

    documents = [
        {
            "task_id": "task-1",
            "batch_id": "batch-1",
            "batch_index": 1,
            "batch_total": 2,
            "status": "completed",
        },
        {
            "task_id": "task-2",
            "batch_id": "batch-1",
            "batch_index": 2,
            "batch_total": 2,
            "status": "error",
        },
    ]
    db = _Db(documents)

    claim = await tasks_dao.claim_completed_batch_notification(db, batch_id="batch-1")
    assert claim is not None
    assert await tasks_dao.claim_completed_batch_notification(db, batch_id="batch-1") is None
    _, owner_task_id, claim_token = claim
    assert await tasks_dao.complete_batch_notification_claim(
        db,
        owner_task_id=owner_task_id,
        claim_token=claim_token,
    ) is True
    assert await tasks_dao.claim_completed_batch_notification(db, batch_id="batch-1") is None

    pending_db = _Db(
        [
            {
                "task_id": "task-3",
                "batch_id": "batch-2",
                "batch_index": 1,
                "batch_total": 1,
                "status": "pending",
            }
        ]
    )
    assert await tasks_dao.claim_completed_batch_notification(
        pending_db,
        batch_id="batch-2",
    ) is None
