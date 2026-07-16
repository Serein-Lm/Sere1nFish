"""Configuration and in-memory observability tests."""

from __future__ import annotations

import asyncio
import time

import pytest


class _DeleteResult:
    def __init__(self, deleted_count: int) -> None:
        self.deleted_count = deleted_count


class _FakeCursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs
        self._idx = 0

    def sort(self, *_args):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._docs):
            raise StopAsyncIteration
        item = self._docs[self._idx]
        self._idx += 1
        return dict(item)


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    @staticmethod
    def _match(doc: dict, query: dict) -> bool:
        for key, expected in query.items():
            if isinstance(expected, dict) and "$exists" in expected:
                exists = key in doc
                if exists != expected["$exists"]:
                    return False
            elif doc.get(key) != expected:
                return False
        return True

    async def find_one(self, query: dict):
        for doc in self.docs:
            if self._match(doc, query):
                return dict(doc)
        return None

    async def find_one_and_update(self, query: dict, update: dict, **_kwargs):
        for idx, doc in enumerate(self.docs):
            if self._match(doc, query):
                merged = {**doc, **update.get("$set", {})}
                self.docs[idx] = merged
                return dict(merged)
        doc = {**update.get("$setOnInsert", {}), **update.get("$set", {})}
        self.docs.append(doc)
        return dict(doc)

    async def delete_one(self, query: dict):
        for idx, doc in enumerate(self.docs):
            if self._match(doc, query):
                self.docs.pop(idx)
                return _DeleteResult(1)
        return _DeleteResult(0)

    def find(self, query: dict):
        return _FakeCursor([dict(doc) for doc in self.docs if self._match(doc, query)])


class _FakeDB:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str):
        self.collections.setdefault(name, _FakeCollection())
        return self.collections[name]


class _ExplodingDB:
    def __getitem__(self, name: str):  # pragma: no cover - failure path
        raise AssertionError(f"unexpected DB access: {name}")


def test_sensitive_config_is_encrypted_and_masked() -> None:
    from api.utils.config_crypto import (
        decrypt_config,
        encrypt_config,
        is_encrypted_value,
        mask_sensitive_config,
    )

    raw = {
        "api_key": "sk-secret",
        "api_token": "token-secret",
        "nested": {"token": "tok-secret", "plain": "visible"},
        "items": [{"password": "pw-secret"}],
    }

    encrypted = encrypt_config(raw)
    assert is_encrypted_value(encrypted["api_key"])
    assert is_encrypted_value(encrypted["api_token"])
    assert is_encrypted_value(encrypted["nested"]["token"])
    assert encrypted["nested"]["plain"] == "visible"
    assert is_encrypted_value(encrypted["items"][0]["password"])
    assert "sk-secret" not in str(encrypted)
    assert "token-secret" not in str(encrypted)

    assert decrypt_config(encrypted) == raw
    masked = mask_sensitive_config(decrypt_config(encrypted))
    assert masked["api_key"] == "***"
    assert masked["api_token"] == "toke...cret"
    assert masked["nested"]["token"] == "***"
    assert masked["nested"]["plain"] == "visible"


def test_asset_intelligence_tools_are_registered() -> None:
    from api.dao.config import CONFIG_CATEGORIES

    tool_names = CONFIG_CATEGORIES["tools"]["sub_keys"]
    assert "fofa" in tool_names
    assert "hunter" in tool_names


def test_load_config_without_path_does_not_read_default_config(monkeypatch, tmp_path) -> None:
    from Sere1nGraph.graph.config.loader import load_config

    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"runtime":{"api_key":"file-key","models":{"default":"file-model"}}}',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    default_cfg = load_config()
    assert default_cfg.runtime.api_key is None
    assert default_cfg.runtime.models.default != "file-model"

    with pytest.raises(ValueError, match="前端配置页"):
        load_config(str(config_path))


def test_sync_config_script_is_disabled() -> None:
    from scripts import sync_config

    with pytest.raises(SystemExit, match="旧配置文件同步脚本已下线"):
        sync_config.main()


def test_graph_runtime_rejects_file_config_entrypoint() -> None:
    from Sere1nGraph.graph.main import _load_app_config

    async def run() -> None:
        with pytest.raises(ValueError, match="前端配置页"):
            await _load_app_config("/tmp/config.json")

    asyncio.run(run())


def test_llm_frontend_config_feeds_runtime_app_config() -> None:
    from api.dao.config import build_app_config_from_db_configs

    app_config = build_app_config_from_db_configs(
        {
            "runtime": {
                "agent_timeout": 321,
                "models": {"default": "old-default", "vision": "old-vision"},
            },
            "llm": {
                "api_key": "front-key",
                "base_url": "https://example.test/v1",
                "default_model": "front-default",
                "vision_model": "front-vision",
                "mobile_planner_model": "front-planner",
                "mobile_executor_model": "front-executor",
                "mobile_screen_model": "front-screen",
                "mobile_chat_model": "front-chat",
            },
        }
    )

    assert app_config.runtime.api_key == "front-key"
    assert app_config.runtime.base_url == "https://example.test/v1"
    assert app_config.runtime.agent_timeout == 321
    assert app_config.runtime.models.default == "front-default"
    assert app_config.runtime.models.vision == "front-vision"
    assert app_config.runtime.models.mobile_planner_model == "front-planner"
    assert app_config.runtime.models.mobile_executor_model == "front-executor"
    assert app_config.runtime.models.mobile_screen_model == "front-screen"
    assert app_config.runtime.models.mobile_chat_model == "front-chat"


def test_llm_config_syncs_to_runtime_and_delete_clears_fallbacks() -> None:
    from api.dao import config as config_dao

    async def run() -> None:
        db = _FakeDB()
        await config_dao.set_config(
            db,
            "runtime",
            {
                "api_key": "old-key",
                "base_url": "https://old.example/v1",
                "agent_timeout": 321,
                "models": {
                    "default": "old-default",
                    "vision": "old-vision",
                    "custom_keep": "kept",
                },
            },
        )

        await config_dao.set_llm_config(
            db,
            api_key="front-key",
            default_model="front-default",
            mobile_planner_model="front-planner",
        )

        runtime_doc = await config_dao.get_config(db, "runtime")
        runtime = runtime_doc["config"]
        assert runtime["api_key"] == "front-key"
        assert runtime["base_url"] == "https://old.example/v1"
        assert runtime["agent_timeout"] == 321
        assert runtime["models"]["default"] == "front-default"
        assert runtime["models"]["vision"] == "old-vision"
        assert runtime["models"]["mobile_planner"] == "front-planner"
        assert runtime["models"]["custom_keep"] == "kept"

        assert await config_dao.delete_llm_config(db) is True
        assert await config_dao.get_llm_config(db) == {}

        runtime_after = (await config_dao.get_config(db, "runtime"))["config"]
        assert runtime_after["agent_timeout"] == 321
        assert runtime_after["models"] == {"custom_keep": "kept"}

    asyncio.run(run())


def test_delete_tool_config_removes_root_and_keyed_docs() -> None:
    from api.dao import config as config_dao

    async def run() -> None:
        db = _FakeDB()
        await config_dao.set_tool_config(db, "hunter", "hunter-key")
        await config_dao.set_tool_config(db, "bocha", "bocha-key")

        before = await config_dao.list_tool_configs(db)
        assert set(before) == {"hunter", "bocha"}

        assert await config_dao.delete_tool_config(db, "hunter") is True

        after = await config_dao.list_tool_configs(db)
        assert "hunter" not in after
        assert after["bocha"]["api_key"] == "bocha-key"

    asyncio.run(run())


def test_generic_config_merge_preserves_masked_secrets() -> None:
    from api.routers.config import _merge_config_update

    existing = {
        "api_key": "sk-real-secret",
        "nested": {
            "token": "tok-real-secret",
            "plain": "old",
        },
        "enabled": False,
    }
    incoming = {
        "api_key": "sk-r...cret",
        "nested": {
            "token": "***",
            "plain": "new",
        },
        "enabled": True,
    }

    merged = _merge_config_update(existing, incoming)
    assert merged["api_key"] == "sk-real-secret"
    assert merged["nested"]["token"] == "tok-real-secret"
    assert merged["nested"]["plain"] == "new"
    assert merged["enabled"] is True


def test_token_tracker_turns_work_without_db() -> None:
    from Sere1nGraph.graph.observability.tracker import TokenTracker, UsageRecord

    tracker = TokenTracker()
    assert tracker._db is None

    now = time.time()
    tracker._record(
        UsageRecord(
            model="qwen",
            input_tokens=10,
            output_tokens=5,
            cost_yuan=0.01,
            duration_ms=100,
            timestamp=now,
            project_id="p1",
            task_id="t1",
            turn_id="turn-1",
            run_id="run-1",
            phase="plan",
            agent="planner",
        )
    )
    tracker._record(
        UsageRecord(
            model="qwen",
            input_tokens=3,
            output_tokens=7,
            cost_yuan=0.02,
            duration_ms=50,
            timestamp=now + 1,
            project_id="p1",
            task_id="t1",
            turn_id="turn-1",
            run_id="run-2",
            phase="act",
            agent="executor",
        )
    )
    tracker._record(
        UsageRecord(
            model="qwen",
            input_tokens=99,
            output_tokens=1,
            cost_yuan=1.23,
            duration_ms=10,
            timestamp=now + 2,
            project_id="p2",
            task_id="t2",
            turn_id="turn-2",
            run_id="run-3",
            phase="other",
            agent="other",
        )
    )

    turns = tracker.get_turns(project_id="p1", task_id="t1", limit=10)
    assert len(turns) == 1
    assert turns[0]["turn_id"] == "turn-1"
    assert turns[0]["total_calls"] == 2
    assert turns[0]["total_input_tokens"] == 13
    assert turns[0]["total_output_tokens"] == 12
    assert turns[0]["total_tokens"] == 25
    assert turns[0]["by_phase"]["plan"]["calls"] == 1
    assert turns[0]["by_agent"]["executor"]["output_tokens"] == 7
    assert [call["call_index"] for call in turns[0]["calls"]] == [1, 2]
    assert [call["phase"] for call in turns[0]["calls"]] == ["plan", "act"]
    assert turns[0]["calls"][0]["run_id"] == "run-1"
    assert turns[0]["calls"][1]["total_tokens"] == 10

    assert tracker.evict_records(task_id="t1") == 2
    assert tracker.get_stats(project_id="p1")["total_calls"] == 0
    assert tracker.get_stats(project_id="p2")["total_calls"] == 1

    asyncio.run(tracker.load_history_from_db())
    tracker.start_flusher()
    pending_before_flush = len(tracker._pending)
    tracker._pending.append(UsageRecord(model="pending"))
    asyncio.run(tracker.flush_pending())
    assert len(tracker._pending) == pending_before_flush + 1
    asyncio.run(tracker.drain())


def test_legacy_stats_routes_use_memory_tracker(monkeypatch) -> None:
    from Sere1nGraph.graph.observability import tracker as tracker_module
    from Sere1nGraph.graph.observability.tracker import TokenTracker, UsageRecord
    from api.routers import project_api

    tracker = TokenTracker()
    monkeypatch.setattr(tracker_module, "_global_tracker", tracker)
    monkeypatch.setattr(
        project_api,
        "get_db",
        lambda: (_ for _ in ()).throw(AssertionError("stats routes touched DB")),
    )

    now = time.time()
    tracker._record(
        UsageRecord(
            model="qwen",
            input_tokens=20,
            output_tokens=10,
            cost_yuan=0.03,
            duration_ms=110,
            timestamp=now,
            project_id="legacy-p1",
            task_id="legacy-t1",
            turn_id="legacy-turn",
            run_id="legacy-run-1",
            phase="plan",
            agent="planner",
        )
    )
    tracker._record(
        UsageRecord(
            model="qwen-vl",
            input_tokens=5,
            output_tokens=15,
            cost_yuan=0.04,
            duration_ms=210,
            timestamp=now + 1,
            project_id="legacy-p1",
            task_id="legacy-t1",
            turn_id="legacy-turn",
            run_id="legacy-run-2",
            phase="act",
            agent="executor",
        )
    )

    async def run() -> None:
        global_stats = await project_api.get_global_stats()
        project_stats = await project_api.get_project_stats("legacy-p1")
        task_stats = await project_api.get_task_stats("legacy-t1")
        records = await project_api.get_stats_records(
            project_id="legacy-p1", task_id="legacy-t1", limit=10
        )

        assert global_stats["global"]["total_calls"] == 2
        assert global_stats["projects"][0]["project_id"] == "legacy-p1"
        assert project_stats["stats"]["total_tokens"] == 50
        assert project_stats["tasks"][0]["task_id"] == "legacy-t1"
        assert task_stats["stats"]["by_agent"]["executor"]["output_tokens"] == 15
        assert {item["agent"] for item in task_stats["agents"]} == {
            "executor",
            "planner",
        }
        assert [item["run_id"] for item in records["records"]] == [
            "legacy-run-1",
            "legacy-run-2",
        ]
        assert records["records"][1]["langgraph_node"] == ""

    asyncio.run(run())


def test_observability_logger_is_memory_only(tmp_path, monkeypatch) -> None:
    from core.observability.logs import ObservabilityLogger

    monkeypatch.setenv("OBS_LOG_DIR", str(tmp_path / "observability"))
    logger = ObservabilityLogger(db=_ExplodingDB())
    logger.set_db(_ExplodingDB())
    assert logger._db is None

    log_id = logger.log(
        "hello",
        project_id="p1",
        task_id="t1",
        source="pytest",
        level="warning",
        event="unit",
        data={"x": 1},
    )
    items, total = logger.query_logs(project_id="p1", min_level="info", limit=10)
    assert total == 1
    assert items[0]["log_id"] == log_id
    assert items[0]["data"] == {"x": 1}
    assert logger.count_by_level(project_id="p1") == {"warning": 1}

    assert logger.evict_logs(task_id="t1") == 1
    _, total_after = logger.query_logs(project_id="p1", limit=10)
    assert total_after == 0

    logger.start_flusher()
    logger._pending.append({"x": 1})
    asyncio.run(logger.flush_pending())
    assert logger._pending == []
    asyncio.run(logger.drain())
