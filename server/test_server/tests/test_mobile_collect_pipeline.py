"""手机采集 Pipeline 端到端(mock 设备/LLM)集成测试。

在不依赖真机与在线模型的前提下, 验证采集全链路:
  导航 → 截屏 → 结构化分析 → 增量入库 → 增量通知,
并验证设备 acquire/release 与取消(request_stop)幂等。
"""
import asyncio

import pytest


class _FakeShot:
    base64_data = "QUJD"
    width = 100
    height = 200


class _FakeCap:
    screenshot = _FakeShot()


class _RecordsColl:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    async def find_one(self, query, projection=None):
        d = self.docs.get(query.get("record_id"))
        return dict(d) if d else None

    async def update_one(self, query, update, upsert=False):
        rid = query.get("record_id")
        doc = self.docs.get(rid)
        if doc is None:
            if not upsert:
                return
            doc = {}
            doc.update(update.get("$setOnInsert", {}))
        doc.update(update.get("$set", {}))
        for k, v in update.get("$addToSet", {}).items():
            arr = doc.setdefault(k, [])
            values = v.get("$each", []) if isinstance(v, dict) else [v]
            for value in values:
                if value not in arr:
                    arr.append(value)
        self.docs[rid] = doc


class _FakeDB:
    def __init__(self) -> None:
        self._c: dict[str, _RecordsColl] = {}

    def __getitem__(self, name):
        return self._c.setdefault(name, _RecordsColl())


class _FakePool:
    _inst = None
    events: list = []

    @classmethod
    def get_instance(cls):
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def acquire(self, device_key, owner, note=None, device_id=None):
        type(self).events.append(("acquire", device_key))

    def release(self, device_key, owner, force=False):
        type(self).events.append(("release", device_key))


def _patch_pipeline(monkeypatch, *, analyze_returns):
    from core.mobile.collect import pipeline as pl

    async def _fake_run_planned(*a, **k):
        if False:
            yield None
        return

    async def _fake_capture(device_id, manager=None):
        return _FakeCap()

    async def _fake_save(db, **k):
        return {"screenshot_id": "shot1", "url": "/api/v1/mobile/screenshots/shot1/image"}

    async def _fake_analyze(image_base64, **k):
        return list(analyze_returns)

    async def _fake_triage(image_base64, **k):
        return [
            {
                "fields": dict(record),
                "score": 80,
                "subject_match": 80,
                "score_reason": "test",
                "source_url": None,
            }
            for record in analyze_returns
        ]

    _FakePool.events = []
    _FakePool._inst = None

    monkeypatch.setattr(pl, "resolve_device_key", lambda device_id: f"dk-{device_id}")
    monkeypatch.setattr(pl, "DevicePool", _FakePool)
    monkeypatch.setattr(pl, "run_planned_task", _fake_run_planned)
    monkeypatch.setattr(pl, "capture_ready_screen", _fake_capture)
    monkeypatch.setattr(pl, "_do_swipe", lambda device_id: None)
    monkeypatch.setattr(pl, "MobileDeviceManager", lambda *a, **k: object())
    monkeypatch.setattr(pl.ma_dao, "save_screenshot", _fake_save)
    monkeypatch.setattr(pl, "analyze_screenshot", _fake_analyze)
    monkeypatch.setattr(pl, "triage_screenshot", _fake_triage)
    monkeypatch.setattr(pl, "obs_log", lambda *a, **k: "")

    notifies: list = []
    import api.services.notifications as notif

    def _fake_notify(**kwargs):
        notifies.append(kwargs)
        return True

    monkeypatch.setattr(notif, "notify_event_background", _fake_notify)
    return pl, notifies


def _task_def(**over):
    base = {
        "task_def_id": "mct_test",
        "name": "集成测试任务",
        "device_id": "devA",
        "app_name": "微信",
        "keywords": ["kw1"],
        "swipe_times": 0,
        "swipe_interval": 0.01,
        "extract_fields": [
            {"name": "title", "description": "标题", "type": "string"},
            {"name": "author", "description": "作者", "type": "string"},
        ],
        "dedup_key_fields": ["title", "author"],
        "notify_on": "new",
    }
    base.update(over)
    return base


def test_pipeline_full_chain_persists_and_notifies(monkeypatch):
    pl, notifies = _patch_pipeline(monkeypatch, analyze_returns=[{"title": "A", "author": "x"}])
    db = _FakeDB()

    async def scenario():
        return await pl.run_collect_task(db, run_task_id="run-1", project_id="p1", task_def=_task_def())

    result = asyncio.new_event_loop().run_until_complete(scenario())

    assert result["total"] == 1
    assert result["new"] == 1
    assert result["stopped"] is False
    from api.dao.mobile_collect import MOBILE_COLLECT_RECORDS_COLLECTION

    recs = db[MOBILE_COLLECT_RECORDS_COLLECTION].docs
    assert len(recs) == 1
    rec = next(iter(recs.values()))
    assert rec["is_new"] is True
    assert rec["fields"] == {"title": "A", "author": "x"}
    assert rec["screenshot_ids"] == ["shot1"]
    assert len(notifies) == 1
    assert notifies[0]["event"] == "mobile_collect_incremental"
    assert ("acquire", "dk-devA") in _FakePool.events
    assert ("release", "dk-devA") in _FakePool.events
    assert pl.request_stop("run-1") is False
    assert pl.is_running("run-1") is False


def test_pipeline_no_notify_when_notify_on_none(monkeypatch):
    pl, notifies = _patch_pipeline(monkeypatch, analyze_returns=[{"title": "B", "author": "y"}])
    db = _FakeDB()

    async def scenario():
        return await pl.run_collect_task(
            db, run_task_id="run-2", project_id=None, task_def=_task_def(notify_on="none")
        )

    result = asyncio.new_event_loop().run_until_complete(scenario())
    assert result["new"] == 1
    assert notifies == []
    assert ("release", "dk-devA") in _FakePool.events


def test_request_stop_unknown_is_idempotent():
    from core.mobile.collect import pipeline as pl

    assert pl.request_stop("never-started") is False
    assert pl.is_running("never-started") is False


def test_deep_dive_prefers_runtime_extracted_source_url(monkeypatch):
    from core.mobile.collect import pipeline as pl
    from core.mobile.collect.source_links import SourceLinkResult

    emitted: list[tuple[str, dict]] = []

    class _Logger:
        def warning(self, message):
            raise AssertionError(message)

    class _Context:
        logger = _Logger()
        state = {
            "stop_event": asyncio.Event(),
            "device_id": "devA",
            "run_task_id": "run-link",
            "project_id": "projectA",
            "detail_max_swipes": 0,
            "swipe_interval": 0.01,
            "extract_fields": [],
            "app_name": "微信",
            "source_link_strategy": "wechat_copy_link",
        }

        async def emit(self, stage, payload):
            emitted.append((stage, payload))

    async def _capture(ctx, keyword, note):
        return "QUJD", "shot-link", "https://oss.example/shot-link.png"

    async def _analyze_detail(*args, **kwargs):
        return {
            "fields": {"title": "A"},
            "score": 80,
            "source_url": None,
        }

    async def _no_sleep(_seconds):
        return None

    stage = pl._CollectStage()
    monkeypatch.setattr(stage, "_capture_save", _capture)
    monkeypatch.setattr(pl, "_do_tap", lambda *args, **kwargs: None)
    monkeypatch.setattr(pl, "_do_back", lambda *args, **kwargs: None)
    monkeypatch.setattr(pl, "analyze_detail", _analyze_detail)
    monkeypatch.setattr(pl, "obs_log", lambda *args, **kwargs: "")
    monkeypatch.setattr(pl.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(
        pl,
        "extract_source_link",
        lambda device_id, strategy: SourceLinkResult(
            strategy=strategy,
            ok=True,
            url="https://mp.weixin.qq.com/s/runtime-link",
        ),
    )

    asyncio.new_event_loop().run_until_complete(
        stage._deep_dive(
            _Context(),
            "keyword",
            {"tap_x": 100, "tap_y": 200, "score": 80, "subject_match": 90},
        )
    )

    assert len(emitted) == 1
    assert emitted[0][0] == "persist"
    assert emitted[0][1]["source_url"] == "https://mp.weixin.qq.com/s/runtime-link"
