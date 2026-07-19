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
    assert db.tasks.doc["result"] == {"total": 6, "documents": 1}
    assert db.tasks.doc["completed_at"] == db.tasks.doc["updated_at"]
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
    assert db.tasks.doc["error"] == "boom"
    assert db.tasks.doc["completed_at"] == db.tasks.doc["updated_at"]
    assert tracker.depth == 0
