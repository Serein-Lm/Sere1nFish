"""Mobile agent pipeline tests that do not require a real phone."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace


PNG_1X1 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9s"
    "AAAAASUVORK5CYII="
)


async def _cleanup_mobile_project(db, project_id: str, *extra_projects: str) -> None:
    from api.db.collections import (
        AUTO_CHAT_SESSIONS_COLLECTION,
        CONTACT_PROFILES_COLLECTION,
        MOBILE_OPERATION_LOGS_COLLECTION,
        MOBILE_SCREENSHOTS_COLLECTION,
    )
    from api.dao import mobile_artifacts

    projects = [project_id, *extra_projects]
    screenshots = []
    for pid in projects:
        screenshots.extend(await mobile_artifacts.list_screenshots(db, project_id=pid, limit=500))
    await db[MOBILE_SCREENSHOTS_COLLECTION].delete_many({"project_id": {"$in": projects}})
    await db[MOBILE_OPERATION_LOGS_COLLECTION].delete_many({"project_id": {"$in": projects}})
    await db[AUTO_CHAT_SESSIONS_COLLECTION].delete_many({"project_id": {"$in": projects}})
    await db[CONTACT_PROFILES_COLLECTION].delete_many(
        {
            "$or": [
                {"project_id": {"$in": projects}},
                {"project_ids": {"$in": projects}},
            ]
        }
    )
    for doc in screenshots:
        Path(doc.get("file_path") or "").unlink(missing_ok=True)


def test_executor_stream_persists_step_screenshot_and_operation_log(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        from api.dao import mobile_artifacts
        from api.db.mongodb import close_mongo, get_db, init_mongo
        from core.mobile import executor
        import api.services.runtime_config as runtime_config

        project_id = "__pytest_mobile_executor_pipeline__"
        task_id = "task-exec-1"
        contact_id = "wechat:alice"
        monkeypatch.setenv("MOBILE_SCREENSHOT_DIR", str(tmp_path))

        init_mongo()
        db = get_db()
        await mobile_artifacts.ensure_indexes(db)
        await _cleanup_mobile_project(db, project_id)

        class FakeAgent:
            async def stream(self, task: str):
                assert task == "send hello"
                yield {
                    "type": "step",
                    "data": {
                        "step": 1,
                        "action": "tap",
                        "success": True,
                        "finished": False,
                        "message": "tap ok",
                        "screenshot": PNG_1X1,
                    },
                }
                yield {"type": "done", "data": {"success": True, "message": "done", "steps": 1}}

        async def fake_app_config():
            return SimpleNamespace()

        monkeypatch.setattr(runtime_config, "get_runtime_app_config", fake_app_config)
        monkeypatch.setattr(executor, "build_executor_agent", lambda *args, **kwargs: FakeAgent())

        try:
            events = [
                event
                async for event in executor.run_task_stream(
                    "device-1",
                    "send hello",
                    task_id=task_id,
                    project_id=project_id,
                    contact_id=contact_id,
                )
            ]

            assert [event["type"] for event in events] == ["task_start", "step", "done"]
            step = events[1]["data"]
            assert step["screenshot_id"].startswith("ms_")
            assert step["screenshot_url"].endswith(f"{step['screenshot_id']}/image")

            screenshots = await mobile_artifacts.list_screenshots(
                db, project_id=project_id, task_id=task_id, contact_id=contact_id, limit=20
            )
            assert len(screenshots) == 1
            assert screenshots[0]["source"] == "agent_step"
            assert Path(screenshots[0]["file_path"]).exists()

            logs = await mobile_artifacts.list_operations(
                db, project_id=project_id, task_id=task_id, contact_id=contact_id, limit=20
            )
            by_type = {item["operation_type"]: item for item in logs}
            assert {"agent_task", "agent_step", "agent_done"} <= set(by_type)
            assert by_type["agent_step"]["screenshot_id"] == screenshots[0]["screenshot_id"]
            assert by_type["agent_step"]["data"]["screenshot"] == "<stored-on-disk>"
            assert by_type["agent_done"]["status"] == "ok"
        finally:
            await _cleanup_mobile_project(db, project_id)
            close_mongo()

    asyncio.run(run())


def test_read_screen_saves_project_screenshot(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        from api.dao import mobile_artifacts
        from api.db.mongodb import close_mongo, get_db, init_mongo
        from core.mobile import chat_assist

        project_id = "__pytest_mobile_read_screen__"
        monkeypatch.setenv("MOBILE_SCREENSHOT_DIR", str(tmp_path))

        init_mongo()
        db = get_db()
        await mobile_artifacts.ensure_indexes(db)
        await _cleanup_mobile_project(db, project_id)

        class FakeManager:
            def capture(self, device_id: str):
                assert device_id == "device-read"
                return SimpleNamespace(base64_data=PNG_1X1, width=1, height=1)

        class FakeLLM:
            async def ainvoke(self, messages):
                assert messages
                return SimpleNamespace(content="聊天界面，联系人 Alice，最后一条消息是 hello。")

        async def fake_app_config():
            return SimpleNamespace(runtime=SimpleNamespace(models=SimpleNamespace(mobile_screen_model="fake-vl")))

        monkeypatch.setattr(chat_assist, "MobileDeviceManager", FakeManager)
        monkeypatch.setattr(chat_assist, "get_runtime_app_config", fake_app_config)
        monkeypatch.setattr(chat_assist, "create_llm", lambda *args, **kwargs: FakeLLM())

        try:
            result = await chat_assist.read_screen(
                "device-read",
                project_id=project_id,
                task_id="task-read",
                contact_id="wechat:alice",
                source="pytest_read_screen",
            )

            assert result["analysis"].startswith("聊天界面")
            assert result["screenshot_id"].startswith("ms_")
            screenshots = await mobile_artifacts.list_screenshots(
                db, project_id=project_id, task_id="task-read", contact_id="wechat:alice", limit=20
            )
            assert len(screenshots) == 1
            assert screenshots[0]["source"] == "pytest_read_screen"
            assert screenshots[0]["width"] == 1
            assert screenshots[0]["height"] == 1
        finally:
            await _cleanup_mobile_project(db, project_id)
            close_mongo()

    asyncio.run(run())


def test_suggest_stream_omits_saved_screenshot_base64(monkeypatch) -> None:
    async def run() -> None:
        from core.mobile import chat_assist

        async def fake_read_screen(*_args, **_kwargs):
            return {
                "analysis": "聊天界面，联系人 Alice。",
                "screenshot": PNG_1X1,
                "screenshot_id": "ms_saved",
                "screenshot_url": "/api/v1/mobile/screenshots/ms_saved/image",
                "width": 1,
                "height": 1,
            }

        async def fake_app_config():
            return SimpleNamespace()

        async def fake_create_copywriting_agent(*_args, **_kwargs):
            async def agent(_payload):
                yield {"type": "content", "data": "你好"}

            return agent

        monkeypatch.setattr(chat_assist, "read_screen", fake_read_screen)
        monkeypatch.setattr(chat_assist, "get_runtime_app_config", fake_app_config)
        monkeypatch.setattr(chat_assist, "create_copywriting_agent", fake_create_copywriting_agent)
        monkeypatch.setattr(chat_assist, "publish", lambda _event: None)

        events = [
            event
            async for event in chat_assist.suggest_stream(
                "device-suggest",
                project_id="project-suggest",
                task_id="task-suggest",
                contact_id="wechat:alice",
            )
        ]

        screen = next(event for event in events if event["stage"] == "screen")
        assert screen["data"]["analysis"].startswith("聊天界面")
        assert screen["data"]["screenshot_id"] == "ms_saved"
        assert screen["data"]["screenshot_url"].endswith("/ms_saved/image")
        assert "screenshot" not in screen["data"]
        assert events[-1] == {"stage": "done", "data": {"suggestions": "你好"}}

    asyncio.run(run())


def test_profile_analysis_persists_persona_and_project_event(monkeypatch) -> None:
    async def run() -> None:
        from api.dao import contact_profiles
        from api.db.mongodb import close_mongo, get_db, init_mongo
        from core.mobile import profiling

        project_id = "__pytest_mobile_profile_analysis__"
        contact_id = "wechat:alice"
        init_mongo()
        db = get_db()
        await contact_profiles.ensure_indexes(db)
        await _cleanup_mobile_project(db, project_id)

        async def fake_extract(chat_content, existing_persona):
            assert "喜欢安全工具" in chat_content
            return profiling.PersonaExtract(
                name="Alice",
                background="安全研究员",
                personality="谨慎",
                interests=["漏洞研究", "自动化"],
                communication_style="直接",
                summary="关注自动化和漏洞研究",
                tags=["security"],
            )

        events: list[dict] = []
        monkeypatch.setattr(profiling, "_extract_persona", fake_extract)
        monkeypatch.setattr(profiling, "publish", events.append)

        try:
            profile = await profiling.analyze_and_update(
                "device-prof",
                contact_id,
                name=None,
                platform="wechat",
                screen_analysis="Alice 说她喜欢安全工具和漏洞研究。",
                project_id=project_id,
                task_id="task-profile",
            )

            assert profile["contact_id"] == contact_id
            assert profile["project_id"] == project_id
            assert project_id in profile["project_ids"]
            assert profile["name"] == "Alice"
            assert profile["persona"]["background"] == "安全研究员"
            assert "自动化" in profile["persona"]["interests"]
            assert profile["observations"][-1]["project_id"] == project_id
            assert events[-1]["type"] == "profile_updated"
            assert events[-1]["project_id"] == project_id

            listed = await contact_profiles.list_profiles(db, project_id=project_id, limit=10)
            assert [item["contact_id"] for item in listed] == [contact_id]
        finally:
            await _cleanup_mobile_project(db, project_id)
            close_mongo()

    asyncio.run(run())


def test_auto_chat_sessions_are_project_queryable() -> None:
    async def run() -> None:
        from api.dao import auto_chat_sessions
        from api.db.mongodb import close_mongo, get_db, init_mongo

        project_id = "__pytest_mobile_auto_chat_sessions__"
        other_project_id = "__pytest_mobile_auto_chat_sessions_other__"
        init_mongo()
        db = get_db()
        await auto_chat_sessions.ensure_indexes(db)
        await _cleanup_mobile_project(db, project_id, other_project_id)

        try:
            await auto_chat_sessions.upsert_session(
                db,
                {
                    "task_id": "auto-1",
                    "device_id": "device-auto",
                    "contact_id": "wechat:alice",
                    "project_id": project_id,
                    "status": "running",
                },
            )
            await auto_chat_sessions.upsert_session(
                db,
                {
                    "task_id": "auto-2",
                    "device_id": "device-auto",
                    "contact_id": "wechat:bob",
                    "project_id": other_project_id,
                    "status": "running",
                },
            )

            sessions = await auto_chat_sessions.list_sessions(db, project_id=project_id, limit=20)
            assert [item["task_id"] for item in sessions] == ["auto-1"]
        finally:
            await _cleanup_mobile_project(db, project_id, other_project_id)
            close_mongo()

    asyncio.run(run())
