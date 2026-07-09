"""手机采集任务通用框架 — 单元测试。

覆盖计划验证要求:
- mobile_collect DAO 的增量 upsert 语义 (new / same / changed / 稳定 record_id);
- schedules 的到期计算与 next_run 逻辑 (interval / cron)。

这些用例不依赖真实 MongoDB: 用最小 FakeCollection 模拟 find_one/update_one(upsert)。
"""
import asyncio
from datetime import datetime, timezone

import pytest


# ── 最小异步集合桩(仅支持本测试所需的 find_one / update_one upsert) ──
class _FakeCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    async def find_one(self, query: dict, projection: dict | None = None):
        rid = query.get("record_id")
        doc = self.docs.get(rid)
        if doc is None:
            return None
        return dict(doc)

    async def update_one(self, query: dict, update: dict, upsert: bool = False):
        rid = query.get("record_id")
        existing = self.docs.get(rid)
        if existing is None:
            if not upsert:
                return
            existing = {}
            existing.update(update.get("$setOnInsert", {}))
        existing.update(update.get("$set", {}))
        add = update.get("$addToSet", {})
        for key, val in add.items():
            arr = existing.setdefault(key, [])
            if val not in arr:
                arr.append(val)
        self.docs[rid] = existing


class _FakeDB:
    def __init__(self) -> None:
        self._colls: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self._colls.setdefault(name, _FakeCollection())


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── DAO: 稳定 record_id ─────────────────────────────────
def test_stable_record_id_uses_dedup_keys_order_independent():
    from api.dao import mobile_collect as dao

    a = dao._stable_record_id("t1", {"title": "x", "author": "y", "likes": "9"}, ["title", "author"])
    # 字段顺序不同 / 非去重键字段变化 → 同一稳定 id
    b = dao._stable_record_id("t1", {"author": "y", "title": "x", "likes": "999"}, ["author", "title"])
    assert a == b
    assert a.startswith("mcr_")
    # 去重键值不同 → 不同 id
    c = dao._stable_record_id("t1", {"title": "z", "author": "y"}, ["title", "author"])
    assert a != c
    # 不同任务 → 不同 id
    d = dao._stable_record_id("t2", {"title": "x", "author": "y"}, ["title", "author"])
    assert a != d


def test_stable_record_id_without_dedup_keys_uses_full_content():
    from api.dao import mobile_collect as dao

    a = dao._stable_record_id("t1", {"k": "1"}, [])
    b = dao._stable_record_id("t1", {"k": "1"}, [])
    c = dao._stable_record_id("t1", {"k": "2"}, [])
    assert a == b
    assert a != c


# ── DAO: 增量 upsert 语义 ───────────────────────────────
def test_upsert_record_incremental_semantics():
    from api.dao import mobile_collect as dao

    db = _FakeDB()
    dedup = ["title"]

    async def scenario():
        # 首次插入 → is_new
        r1 = await dao.upsert_record(
            db, task_def_id="t1", project_id="p1",
            fields={"title": "A", "likes": "1"}, dedup_key_fields=dedup, keyword="k",
        )
        # 相同内容再入 → 既非 new 也非 changed
        r2 = await dao.upsert_record(
            db, task_def_id="t1", project_id="p1",
            fields={"title": "A", "likes": "1"}, dedup_key_fields=dedup, keyword="k",
        )
        # 同一去重键但内容变化 → is_changed
        r3 = await dao.upsert_record(
            db, task_def_id="t1", project_id="p1",
            fields={"title": "A", "likes": "99"}, dedup_key_fields=dedup, keyword="k",
        )
        return r1, r2, r3

    r1, r2, r3 = _run(scenario())

    assert r1["is_new"] is True and r1["is_changed"] is False
    assert r2["is_new"] is False and r2["is_changed"] is False
    assert r3["is_new"] is False and r3["is_changed"] is True
    # 三次操作命中同一稳定 record_id
    assert r1["record_id"] == r2["record_id"] == r3["record_id"]


def test_upsert_record_first_seen_preserved_on_update():
    from api.dao import mobile_collect as dao

    db = _FakeDB()

    async def scenario():
        await dao.upsert_record(
            db, task_def_id="t1", project_id=None,
            fields={"title": "A"}, dedup_key_fields=["title"],
        )
        coll = db[dao.MOBILE_COLLECT_RECORDS_COLLECTION]
        rid = next(iter(coll.docs))
        first_seen = coll.docs[rid]["first_seen"]
        await dao.upsert_record(
            db, task_def_id="t1", project_id=None,
            fields={"title": "A", "extra": "changed"}, dedup_key_fields=["title"],
        )
        return first_seen, coll.docs[rid]["first_seen"]

    before, after = _run(scenario())
    assert before == after  # first_seen 不因更新而改变


# ── 调度: interval next_run ─────────────────────────────
def test_compute_next_run_interval():
    from api.dao import schedules

    after = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    nxt = schedules.compute_next_run({"type": "interval", "interval_seconds": 120}, after=after)
    assert (nxt - after).total_seconds() == 120


def test_compute_next_run_interval_min_floor():
    from api.dao import schedules

    after = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    # 低于最小值 30s 会被抬到 30s
    nxt = schedules.compute_next_run({"type": "interval", "interval_seconds": 5}, after=after)
    assert (nxt - after).total_seconds() == 30


# ── 调度: cron next_run ─────────────────────────────────
def test_compute_next_run_cron_daily():
    from api.dao import schedules

    # 每天 08:00; 当前 09:00 → 次日 08:00
    after = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
    nxt = schedules.compute_next_run({"type": "cron", "cron": "0 8 * * *"}, after=after)
    assert (nxt.year, nxt.month, nxt.day, nxt.hour, nxt.minute) == (2024, 1, 2, 8, 0)


def test_compute_next_run_cron_list_same_day():
    from api.dao import schedules

    # 每天 9,20 点; 当前 10:00 → 当天 20:00
    after = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    nxt = schedules.compute_next_run({"type": "cron", "cron": "0 9,20 * * *"}, after=after)
    assert (nxt.day, nxt.hour, nxt.minute) == (1, 20, 0)


def test_compute_next_run_cron_step_minutes():
    from api.dao import schedules

    # 每 15 分钟; 当前 00:07 → 00:15
    after = datetime(2024, 1, 1, 0, 7, 0, tzinfo=timezone.utc)
    nxt = schedules.compute_next_run({"type": "cron", "cron": "*/15 * * * *"}, after=after)
    assert (nxt.hour, nxt.minute) == (0, 15)


def test_compute_next_run_cron_weekday():
    from api.dao import schedules

    # 每周一 09:00; 2024-01-01 是周一, 当前 10:00 → 下周一 (01-08) 09:00
    after = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    nxt = schedules.compute_next_run({"type": "cron", "cron": "0 9 * * 1"}, after=after)
    assert (nxt.month, nxt.day, nxt.hour) == (1, 8, 9)
    assert nxt.weekday() == 0  # Monday


def test_validate_trigger_rejects_bad_cron():
    from api.dao import schedules

    with pytest.raises(ValueError):
        schedules.validate_trigger({"type": "cron", "cron": "bad expr"})
    with pytest.raises(ValueError):
        schedules.validate_trigger({"type": "cron", "cron": ""})
