import asyncio


class _TaskCollection:
    def __init__(self) -> None:
        self.doc = {"status": "pending", "attempt_count": 0, "recovery_count": 0}

    async def find_one_and_update(self, _query, update, **_kwargs):
        self.doc.update(update.get("$set", {}))
        for key, amount in update.get("$inc", {}).items():
            self.doc[key] = self.doc.get(key, 0) + amount
        for key in update.get("$unset", {}):
            self.doc.pop(key, None)
        return dict(self.doc)

    async def find_one(self, query, _projection=None):
        for key in ("status", "runtime_id"):
            if key in query and self.doc.get(key) != query[key]:
                return None
        return dict(self.doc)

    async def update_one(self, _query, update):
        self.doc.update(update.get("$set", {}))
        for key in update.get("$unset", {}):
            self.doc.pop(key, None)

        class _Result:
            modified_count = 1

        return _Result()


class _Db:
    def __init__(self) -> None:
        self.tasks = _TaskCollection()

    def __getitem__(self, _name):
        return self.tasks


class _Tracker:
    def __init__(self) -> None:
        self.depth = 0

    def push_context(self, **_kwargs):
        self.depth += 1

    def pop_context(self):
        self.depth -= 1


def test_execute_task_persists_dispatch_result_and_completion_time(monkeypatch):
    from api.services import project_task_runtime
    from Sere1nGraph.graph import observability as graph_observability

    db = _Db()
    tracker = _Tracker()

    async def dispatcher(_task_id, _project_id, _params):
        return {"total": 6, "documents": 1}

    monkeypatch.setattr(project_task_runtime, "get_db", lambda: db)
    monkeypatch.setattr(
        project_task_runtime,
        "_TASK_DISPATCHERS",
        {"mobile_collect": dispatcher},
    )
    monkeypatch.setattr(graph_observability, "get_global_tracker", lambda: tracker)
    monkeypatch.setattr(project_task_runtime, "obs_log", lambda *args, **kwargs: None)

    asyncio.run(
        project_task_runtime.execute_project_task(
            "task-1", "project-1", "mobile_collect", {"task_def_id": "def-1"}
        )
    )

    assert db.tasks.doc["status"] == "completed"
    assert db.tasks.doc["progress.stage"] == "completed"
    assert db.tasks.doc["progress.message"] == "任务已完成"
    assert db.tasks.doc["result_status"] == "completed"
    assert db.tasks.doc["result"] == {"total": 6, "documents": 1}
    assert db.tasks.doc["completed_at"] == db.tasks.doc["updated_at"]
    assert tracker.depth == 0


def test_execute_task_preserves_partial_result_status(monkeypatch):
    from api.services import project_task_runtime
    from Sere1nGraph.graph import observability as graph_observability

    db = _Db()
    tracker = _Tracker()

    async def dispatcher(_task_id, _project_id, _params):
        return {
            "status": "partial",
            "incomplete_sources": ["website_documents"],
        }

    monkeypatch.setattr(project_task_runtime, "get_db", lambda: db)
    monkeypatch.setattr(
        project_task_runtime,
        "_TASK_DISPATCHERS",
        {"company_scan": dispatcher},
    )
    monkeypatch.setattr(graph_observability, "get_global_tracker", lambda: tracker)
    monkeypatch.setattr(project_task_runtime, "obs_log", lambda *args, **kwargs: None)

    asyncio.run(
        project_task_runtime.execute_project_task(
            "task-partial", "project-1", "company_scan", {}
        )
    )

    assert db.tasks.doc["status"] == "completed"
    assert db.tasks.doc["result_status"] == "partial"
    assert db.tasks.doc["progress.stage"] == "partial"
    assert db.tasks.doc["progress.message"] == "任务已结束，但部分数据源未完整采集"


def test_execute_task_maps_returned_error_status_to_failed_task(monkeypatch):
    from api.services import project_task_runtime
    from Sere1nGraph.graph import observability as graph_observability

    db = _Db()
    tracker = _Tracker()

    async def dispatcher(_task_id, _project_id, _params):
        return {"status": "error", "error": "all providers unavailable"}

    monkeypatch.setattr(project_task_runtime, "get_db", lambda: db)
    monkeypatch.setattr(
        project_task_runtime,
        "_TASK_DISPATCHERS",
        {"scholar_contact": dispatcher},
    )
    monkeypatch.setattr(graph_observability, "get_global_tracker", lambda: tracker)
    monkeypatch.setattr(project_task_runtime, "obs_log", lambda *args, **kwargs: None)

    asyncio.run(
        project_task_runtime.execute_project_task(
            "task-provider-error", "project-1", "scholar_contact", {}
        )
    )

    assert db.tasks.doc["status"] == "error"
    assert db.tasks.doc["result_status"] == "error"
    assert db.tasks.doc["progress.stage"] == "error"
    assert db.tasks.doc["error"] == "all providers unavailable"
    assert tracker.depth == 0


def test_execute_task_marks_error_completion_time(monkeypatch):
    from api.services import project_task_runtime
    from Sere1nGraph.graph import observability as graph_observability

    db = _Db()
    tracker = _Tracker()

    async def dispatcher(_task_id, _project_id, _params):
        raise RuntimeError("boom")

    monkeypatch.setattr(project_task_runtime, "get_db", lambda: db)
    monkeypatch.setattr(
        project_task_runtime,
        "_TASK_DISPATCHERS",
        {"mobile_collect": dispatcher},
    )
    monkeypatch.setattr(graph_observability, "get_global_tracker", lambda: tracker)
    monkeypatch.setattr(project_task_runtime, "obs_log", lambda *args, **kwargs: None)

    asyncio.run(
        project_task_runtime.execute_project_task(
            "task-2", "project-1", "mobile_collect", {"task_def_id": "def-1"}
        )
    )

    assert db.tasks.doc["status"] == "error"
    assert db.tasks.doc["progress.stage"] == "error"
    assert db.tasks.doc["progress.message"] == "boom"
    assert db.tasks.doc["error"] == "boom"
    assert db.tasks.doc["completed_at"] == db.tasks.doc["updated_at"]
    assert tracker.depth == 0


def test_execute_task_waits_for_llm_capacity_without_marking_failure(monkeypatch):
    from api.services import notifications, project_task_runtime
    from Sere1nGraph.graph import observability as graph_observability
    from core import llm_capacity

    db = _Db()
    tracker = _Tracker()
    calls = 0
    waits: list[int] = []

    async def dispatcher(_task_id, _project_id, _params):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise llm_capacity.LLMCapacityUnavailableError(
                retry_after_seconds=10,
                incident_id=7,
            )
        return {"resumed": True}

    class _Guard:
        async def wait_for_retry_window(self, incident_id):
            waits.append(incident_id)

    monkeypatch.setattr(project_task_runtime, "get_db", lambda: db)
    monkeypatch.setattr(
        project_task_runtime,
        "_TASK_DISPATCHERS",
        {"company_scan": dispatcher},
    )
    monkeypatch.setattr(
        project_task_runtime,
        "_NOTIFIED_LLM_CAPACITY_INCIDENTS",
        set(),
    )
    monkeypatch.setattr(graph_observability, "get_global_tracker", lambda: tracker)
    monkeypatch.setattr(project_task_runtime, "obs_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(notifications, "notify_event_background", lambda **_kwargs: None)
    monkeypatch.setattr(llm_capacity, "get_global_llm_capacity_guard", lambda: _Guard())

    asyncio.run(
        project_task_runtime.execute_project_task(
            "task-3",
            "project-1",
            "company_scan",
            {"company_name": "测试公司"},
        )
    )

    assert calls == 2
    assert waits == [7]
    assert db.tasks.doc["status"] == "completed"
    assert db.tasks.doc["result"] == {"resumed": True}
    assert "error" not in db.tasks.doc
    assert tracker.depth == 0


def test_execute_task_pause_cancels_only_registered_execution(monkeypatch):
    from api.services import project_task_runtime
    from Sere1nGraph.graph import observability as graph_observability

    db = _Db()
    tracker = _Tracker()

    async def scenario() -> None:
        started = asyncio.Event()

        async def dispatcher(_task_id, _project_id, _params):
            started.set()
            await asyncio.Event().wait()

        async def pause_requested(*_args, **_kwargs):
            return True

        async def mark_paused(*_args, **_kwargs):
            db.tasks.doc["status"] = "paused"
            return True

        monkeypatch.setattr(
            project_task_runtime,
            "_TASK_DISPATCHERS",
            {"company_scan": dispatcher},
        )
        monkeypatch.setattr(
            project_task_runtime.tasks_dao,
            "is_pause_requested",
            pause_requested,
        )
        monkeypatch.setattr(
            project_task_runtime.tasks_dao,
            "mark_task_paused",
            mark_paused,
        )
        execution = asyncio.create_task(
            project_task_runtime.execute_project_task(
                "task-pause",
                "project-1",
                "company_scan",
                {"company_name": "测试公司"},
            )
        )
        await started.wait()
        assert await project_task_runtime.cancel_running_project_task(
            "task-pause",
            wait_timeout=0.2,
        )
        await execution

    monkeypatch.setattr(project_task_runtime, "get_db", lambda: db)
    monkeypatch.setattr(graph_observability, "get_global_tracker", lambda: tracker)
    monkeypatch.setattr(project_task_runtime, "obs_log", lambda *args, **kwargs: None)

    asyncio.run(scenario())

    assert db.tasks.doc["status"] == "paused"
    assert "task-pause" not in project_task_runtime._RUNNING_TASKS
    assert tracker.depth == 0


def test_duplicate_execution_observer_does_not_propagate_manual_pause(monkeypatch):
    from api.services import project_task_runtime
    from Sere1nGraph.graph import observability as graph_observability

    db = _Db()
    tracker = _Tracker()

    async def scenario() -> None:
        started = asyncio.Event()

        async def dispatcher(_task_id, _project_id, _params):
            started.set()
            await asyncio.Event().wait()

        async def pause_requested(*_args, **_kwargs):
            return True

        async def mark_paused(*_args, **_kwargs):
            db.tasks.doc["status"] = "paused"
            return True

        monkeypatch.setattr(
            project_task_runtime,
            "_TASK_DISPATCHERS",
            {"company_scan": dispatcher},
        )
        monkeypatch.setattr(
            project_task_runtime.tasks_dao,
            "is_pause_requested",
            pause_requested,
        )
        monkeypatch.setattr(
            project_task_runtime.tasks_dao,
            "mark_task_paused",
            mark_paused,
        )
        primary = asyncio.create_task(
            project_task_runtime.execute_project_task(
                "task-shared",
                "project-1",
                "company_scan",
                {"company_name": "测试公司"},
            )
        )
        await started.wait()
        observer = asyncio.create_task(
            project_task_runtime.execute_project_task(
                "task-shared",
                "project-1",
                "company_scan",
                {"company_name": "测试公司"},
            )
        )
        await asyncio.sleep(0)
        assert await project_task_runtime.cancel_running_project_task(
            "task-shared",
            wait_timeout=0.2,
        )

        assert await observer is None
        assert await primary is None

    monkeypatch.setattr(project_task_runtime, "get_db", lambda: db)
    monkeypatch.setattr(graph_observability, "get_global_tracker", lambda: tracker)
    monkeypatch.setattr(project_task_runtime, "obs_log", lambda *args, **kwargs: None)

    asyncio.run(scenario())

    assert db.tasks.doc["status"] == "paused"
    assert "task-shared" not in project_task_runtime._RUNNING_TASKS
    assert tracker.depth == 0
