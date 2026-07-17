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
        if rid is not None:
            doc = self.docs.get(rid)
            candidates = [doc] if doc is not None else []
        else:
            candidates = list(self.docs.values())
        for doc in candidates:
            matched = True
            for key, expected in query.items():
                if isinstance(expected, dict) and "$exists" in expected:
                    if (key in doc) != bool(expected["$exists"]):
                        matched = False
                        break
                elif doc.get(key) != expected:
                    matched = False
                    break
            if matched:
                return dict(doc)
        return None

    async def update_one(self, query: dict, update: dict, upsert: bool = False):
        overlap = set(update.get("$set", {})) & set(update.get("$setOnInsert", {}))
        if overlap:
            raise AssertionError(f"conflicting update paths: {sorted(overlap)}")
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
            values = val.get("$each", []) if isinstance(val, dict) else [val]
            for value in values:
                if value not in arr:
                    arr.append(value)
        self.docs[rid] = existing

    async def update_many(self, query: dict, update: dict):
        task_def_id = query.get("task_def_id")
        project_id = query.get("project_id")
        keywords = set((query.get("keyword") or {}).get("$in") or [])
        modified = 0
        for doc in self.docs.values():
            if task_def_id and doc.get("task_def_id") != task_def_id:
                continue
            if project_id and doc.get("project_id") != project_id:
                continue
            if keywords and doc.get("keyword") not in keywords:
                continue
            if doc.get("target_id") not in (None, ""):
                continue
            doc.update(update.get("$set", {}))
            modified += 1
        return type("UpdateResult", (), {"modified_count": modified})()


class _FakeDB:
    def __init__(self) -> None:
        self._colls: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self._colls.setdefault(name, _FakeCollection())


def _run(coro):
    return asyncio.run(coro)


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


def test_backfill_task_target_only_updates_unassigned_records():
    from api.dao import mobile_collect as dao

    db = _FakeDB()
    coll = db[dao.MOBILE_COLLECT_RECORDS_COLLECTION]
    coll.docs = {
        "missing": {"task_def_id": "task-1"},
        "empty": {"task_def_id": "task-1", "target_id": ""},
        "assigned": {"task_def_id": "task-1", "target_id": "target-old"},
        "other-task": {"task_def_id": "task-2"},
    }

    changed = _run(
        dao.backfill_task_target(
            db,
            task_def_id="task-1",
            target_id="target-new",
            target_name="新目标",
        )
    )

    assert changed == 2
    assert coll.docs["missing"]["target_id"] == "target-new"
    assert coll.docs["empty"]["target_name"] == "新目标"
    assert coll.docs["assigned"]["target_id"] == "target-old"
    assert "target_id" not in coll.docs["other-task"]


def test_backfill_project_target_recovers_deleted_task_records_by_keyword():
    from api.dao import mobile_collect as dao

    db = _FakeDB()
    coll = db[dao.MOBILE_COLLECT_RECORDS_COLLECTION]
    coll.docs = {
        "match": {
            "project_id": "project-1",
            "keyword": "目标公司 招标",
            "task_def_id": "deleted-task",
        },
        "other-keyword": {
            "project_id": "project-1",
            "keyword": "其他公司 招标",
        },
        "other-project": {
            "project_id": "project-2",
            "keyword": "目标公司 招标",
        },
    }

    changed = _run(
        dao.backfill_project_target_by_keywords(
            db,
            project_id="project-1",
            keywords=["目标公司 招标"],
            target_id="target-1",
            target_name="目标公司",
        )
    )

    assert changed == 1
    assert coll.docs["match"]["target_id"] == "target-1"
    assert "target_id" not in coll.docs["other-keyword"]
    assert "target_id" not in coll.docs["other-project"]


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


def test_upsert_record_treats_new_source_url_as_content_change():
    from api.dao import mobile_collect as dao

    db = _FakeDB()

    async def scenario():
        first = await dao.upsert_record(
            db,
            task_def_id="t1",
            project_id=None,
            fields={"title": "A"},
            dedup_key_fields=["title"],
        )
        linked = await dao.upsert_record(
            db,
            task_def_id="t1",
            project_id=None,
            fields={"title": "A"},
            dedup_key_fields=["title"],
            source_url="https://mp.weixin.qq.com/s/demo",
        )
        same = await dao.upsert_record(
            db,
            task_def_id="t1",
            project_id=None,
            fields={"title": "A"},
            dedup_key_fields=["title"],
            source_url="https://mp.weixin.qq.com/s/demo",
        )
        return first, linked, same

    first, linked, same = _run(scenario())
    assert first["is_new"] is True
    assert linked["is_changed"] is True
    assert same["is_changed"] is False


def test_source_document_enriches_existing_list_record_without_duplicate():
    from api.dao import mobile_collect as dao

    db = _FakeDB()

    async def scenario():
        shallow = await dao.upsert_record(
            db,
            task_def_id="t1",
            project_id="p1",
            fields={"title": "同一文章", "account": "测试公众号", "summary": "列表摘要"},
            dedup_key_fields=["title", "account"],
            screenshot_ids=["list-shot"],
        )
        detail = await dao.upsert_record(
            db,
            task_def_id="t1",
            project_id="p1",
            fields={"title": "同一文章（全文标题）", "account": "测试公众号", "content": "完整正文"},
            dedup_key_fields=["title", "account"],
            source_document_id="doc-1",
            source_document_version_id="version-1",
            screenshot_ids=["detail-shot"],
            browser_screenshot_ids=["browser-shot"],
            score=95,
            discovery_fields={"title": "同一文章", "account": "测试公众号", "summary": "列表摘要"},
        )
        rediscovered = await dao.upsert_record(
            db,
            task_def_id="t1",
            project_id="p1",
            fields={"title": "同一文章", "account": "测试公众号", "summary": "新列表摘要"},
            dedup_key_fields=["title", "account"],
            screenshot_ids=["next-list-shot"],
            score=39,
        )
        return shallow, detail, rediscovered

    shallow, detail, rediscovered = _run(scenario())
    coll = db[dao.MOBILE_COLLECT_RECORDS_COLLECTION]
    record = coll.docs[detail["record_id"]]

    assert shallow["record_id"] == detail["record_id"]
    assert rediscovered["record_id"] == detail["record_id"]
    assert rediscovered["is_changed"] is False
    assert len(coll.docs) == 1
    assert detail["is_new"] is False
    assert detail["is_changed"] is True
    assert record["source_document_id"] == "doc-1"
    assert record["fields"]["content"] == "完整正文"
    assert record["discovery_fields"]["summary"] == "新列表摘要"
    assert record["score"] == 95
    assert record["screenshot_ids"] == ["list-shot", "detail-shot", "next-list-shot"]
    assert record["browser_screenshot_ids"] == ["browser-shot"]


def test_source_document_archives_legacy_duplicate_and_merges_evidence():
    from api.dao import mobile_collect as dao

    db = _FakeDB()
    coll = db[dao.MOBILE_COLLECT_RECORDS_COLLECTION]
    fields = {"title": "同一文章", "account": "测试公众号", "content": "完整正文"}
    list_id = dao.stable_record_id(
        "t1", fields, ["title", "account"]
    )
    source_id = dao.stable_record_id(
        "t1", fields, ["title", "account"], source_document_id="doc-1"
    )
    coll.docs = {
        list_id: {
            "record_id": list_id,
            "task_def_id": "t1",
            "fields": {"title": "同一文章", "account": "测试公众号", "summary": "列表摘要"},
            "content_hash": "old-list-hash",
            "screenshot_ids": ["list-shot"],
        },
        source_id: {
            "record_id": source_id,
            "task_def_id": "t1",
            "fields": fields,
            "content_hash": dao._content_hash(fields, "https://mp.weixin.qq.com/s/demo"),
            "source_document_id": "doc-1",
            "screenshot_ids": ["detail-shot"],
            "first_seen": datetime(2026, 7, 17, 2, tzinfo=timezone.utc),
        },
    }
    coll.docs[list_id]["first_seen"] = datetime(
        2026, 7, 17, 1, tzinfo=timezone.utc
    )

    result = _run(
        dao.upsert_record(
            db,
            task_def_id="t1",
            project_id="p1",
            fields=fields,
            dedup_key_fields=["title", "account"],
            source_document_id="doc-1",
            source_url="https://mp.weixin.qq.com/s/demo",
        )
    )

    assert result["record_id"] == source_id
    assert coll.docs[list_id]["superseded_by_record_id"] == source_id
    assert coll.docs[source_id]["discovery_fields"]["summary"] == "列表摘要"
    assert coll.docs[source_id]["screenshot_ids"] == ["detail-shot", "list-shot"]
    assert coll.docs[source_id]["merged_record_ids"] == [list_id]
    assert coll.docs[source_id]["first_seen"] == coll.docs[list_id]["first_seen"]

    rediscovered = _run(
        dao.upsert_record(
            db,
            task_def_id="t1",
            project_id="p1",
            fields={"title": "同一文章", "account": "测试公众号", "summary": "再次发现"},
            dedup_key_fields=["title", "account"],
            screenshot_ids=["next-list-shot"],
        )
    )
    assert rediscovered["record_id"] == source_id
    assert coll.docs[source_id]["fields"]["content"] == "完整正文"
    assert coll.docs[source_id]["discovery_fields"]["summary"] == "再次发现"
    assert coll.docs[source_id]["screenshot_ids"] == [
        "detail-shot",
        "list-shot",
        "next-list-shot",
    ]


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
