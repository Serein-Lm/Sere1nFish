"""Mobile project artifact persistence tests."""

from __future__ import annotations

import asyncio
from pathlib import Path


PNG_1X1 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9s"
    "AAAAASUVORK5CYII="
)


def test_mobile_project_artifacts_roundtrip(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        from api.db.collections import (
            CONTACT_PROFILES_COLLECTION,
            MOBILE_OPERATION_LOGS_COLLECTION,
            MOBILE_SCREENSHOTS_COLLECTION,
            STORAGE_OBJECTS_COLLECTION,
        )
        from api.db.mongodb import close_mongo, get_db, init_mongo
        from api.dao import contact_profiles, mobile_artifacts

        project_id = "__pytest_mobile_project_artifacts__"
        unrelated_project_id = "__pytest_mobile_project_artifacts_other__"
        monkeypatch.setenv("MOBILE_SCREENSHOT_DIR", str(tmp_path))
        monkeypatch.setenv("OBJECT_STORAGE_LOCAL_ROOT", str(tmp_path / "objects"))

        init_mongo()
        db = get_db()
        try:
            await mobile_artifacts.ensure_indexes(db)
            await contact_profiles.ensure_indexes(db)
            await mobile_artifacts.delete_project_artifacts(db, project_id)
            await mobile_artifacts.delete_project_artifacts(db, unrelated_project_id)
            stale = await mobile_artifacts.list_screenshots(
                db, project_id=project_id, limit=100
            )
            await db[MOBILE_SCREENSHOTS_COLLECTION].delete_many(
                {"project_id": {"$in": [project_id, unrelated_project_id]}}
            )
            await db[MOBILE_OPERATION_LOGS_COLLECTION].delete_many(
                {"project_id": {"$in": [project_id, unrelated_project_id]}}
            )
            await db[STORAGE_OBJECTS_COLLECTION].delete_many(
                {"project_id": {"$in": [project_id, unrelated_project_id]}}
            )
            await db[CONTACT_PROFILES_COLLECTION].delete_many(
                {
                    "$or": [
                        {"project_id": {"$in": [project_id, unrelated_project_id]}},
                        {"project_ids": {"$in": [project_id, unrelated_project_id]}},
                    ]
                }
            )
            for doc in stale:
                if doc.get("file_path"):
                    Path(doc["file_path"]).unlink(missing_ok=True)

            screenshot = await mobile_artifacts.save_screenshot(
                db,
                image_base64=PNG_1X1,
                project_id=project_id,
                task_id="task-1",
                device_id="device-1",
                contact_id="contact-1",
                source="pytest",
                width=1,
                height=1,
            )
            assert screenshot["url"].endswith(f"{screenshot['screenshot_id']}/image")
            assert screenshot["file_path"] == ""
            assert screenshot["storage_object_id"] == screenshot["screenshot_id"]
            from api.storage import get_object_storage

            access = await (await get_object_storage()).read_access(screenshot["storage_object_id"])
            assert access.path and access.path.exists()

            await mobile_artifacts.save_screenshot(
                db,
                image_base64=PNG_1X1,
                project_id=unrelated_project_id,
                device_id="device-2",
                source="pytest",
            )

            log = await mobile_artifacts.log_operation(
                db,
                operation_type="tap",
                project_id=project_id,
                task_id="task-1",
                device_id="device-1",
                contact_id="contact-1",
                action="tap",
                data={"x": 1, "y": 2},
                screenshot_id=screenshot["screenshot_id"],
            )
            assert log["operation_id"].startswith("mo_")

            await contact_profiles.upsert_profile(
                db,
                "contact-1",
                {
                    "name": "Contact One",
                    "platform": "wechat",
                    "device_id": "device-1",
                    "persona": {
                        "summary": "project-linked profile",
                        "tags": ["pytest"],
                        "interests": [],
                    },
                    "observations": [],
                },
                project_id=project_id,
            )
            await contact_profiles.append_observation(
                db,
                "contact-1",
                {"source": "pytest", "content": "observed", "ts": "now"},
                project_id=project_id,
            )

            screenshots = await mobile_artifacts.list_screenshots(
                db, project_id=project_id, limit=20
            )
            logs = await mobile_artifacts.list_operations(
                db, project_id=project_id, limit=20
            )
            profiles = await contact_profiles.list_profiles(
                db, project_id=project_id, limit=20
            )
            unrelated_screenshots = await mobile_artifacts.list_screenshots(
                db, project_id="missing-project", limit=20
            )

            assert [item["project_id"] for item in screenshots] == [project_id]
            assert [item["project_id"] for item in logs] == [project_id]
            assert profiles[0]["contact_id"] == "contact-1"
            assert project_id in profiles[0].get("project_ids", [])
            assert profiles[0]["observations"][-1]["project_id"] == project_id
            assert unrelated_screenshots == []
        finally:
            stale = await mobile_artifacts.list_screenshots(
                db, project_id=project_id, limit=100
            )
            stale += await mobile_artifacts.list_screenshots(
                db, project_id=unrelated_project_id, limit=100
            )
            await db[MOBILE_SCREENSHOTS_COLLECTION].delete_many(
                {"project_id": {"$in": [project_id, unrelated_project_id]}}
            )
            await db[MOBILE_OPERATION_LOGS_COLLECTION].delete_many(
                {"project_id": {"$in": [project_id, unrelated_project_id]}}
            )
            await db[STORAGE_OBJECTS_COLLECTION].delete_many(
                {"project_id": {"$in": [project_id, unrelated_project_id]}}
            )
            await db[CONTACT_PROFILES_COLLECTION].delete_many(
                {
                    "$or": [
                        {"project_id": {"$in": [project_id, unrelated_project_id]}},
                        {"project_ids": {"$in": [project_id, unrelated_project_id]}},
                    ]
                }
            )
            close_mongo()

    asyncio.run(run())


def test_mobile_event_bus_project_filter() -> None:
    from core.mobile.events import EventBus

    bus = EventBus(history=10)
    bus.publish({"type": "x", "project_id": "p1", "device_id": "d1", "data": {}})
    bus.publish({"type": "x", "project_id": "p2", "device_id": "d1", "data": {}})
    bus.publish({"type": "y", "project_id": "p1", "device_id": "d2", "data": {}})

    events = bus.recent(project_id="p1", types={"x"}, limit=10)
    assert len(events) == 1
    assert events[0]["project_id"] == "p1"
    assert events[0]["type"] == "x"


def test_mobile_project_routes_return_linked_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    async def run() -> None:
        from starlette.responses import FileResponse

        from api.dao import auto_chat_sessions, contact_profiles, mobile_artifacts
        from api.db.collections import (
            AUTO_CHAT_SESSIONS_COLLECTION,
            CONTACT_PROFILES_COLLECTION,
            MOBILE_OPERATION_LOGS_COLLECTION,
            MOBILE_SCREENSHOTS_COLLECTION,
            STORAGE_OBJECTS_COLLECTION,
        )
        from api.db.mongodb import close_mongo, get_db, init_mongo
        from api.routers import mobile as mobile_router

        project_id = "__pytest_mobile_project_routes__"
        unrelated_project_id = "__pytest_mobile_project_routes_other__"
        contact_id = "wechat:route-contact"
        unrelated_contact_id = "wechat:route-other"
        screenshot_path: Path | None = None

        monkeypatch.setenv("MOBILE_SCREENSHOT_DIR", str(tmp_path))
        monkeypatch.setenv("OBJECT_STORAGE_LOCAL_ROOT", str(tmp_path / "objects"))

        init_mongo()
        db = get_db()
        await mobile_artifacts.ensure_indexes(db)
        await contact_profiles.ensure_indexes(db)
        await auto_chat_sessions.ensure_indexes(db)

        try:
            await mobile_artifacts.delete_project_artifacts(db, project_id)
            await mobile_artifacts.delete_project_artifacts(db, unrelated_project_id)
            stale = await mobile_artifacts.list_screenshots(
                db, project_id=project_id, limit=100
            )
            stale += await mobile_artifacts.list_screenshots(
                db, project_id=unrelated_project_id, limit=100
            )
            await db[MOBILE_SCREENSHOTS_COLLECTION].delete_many(
                {"project_id": {"$in": [project_id, unrelated_project_id]}}
            )
            await db[MOBILE_OPERATION_LOGS_COLLECTION].delete_many(
                {"project_id": {"$in": [project_id, unrelated_project_id]}}
            )
            await db[STORAGE_OBJECTS_COLLECTION].delete_many(
                {"project_id": {"$in": [project_id, unrelated_project_id]}}
            )
            await db[AUTO_CHAT_SESSIONS_COLLECTION].delete_many(
                {"project_id": {"$in": [project_id, unrelated_project_id]}}
            )
            await db[CONTACT_PROFILES_COLLECTION].delete_many(
                {
                    "$or": [
                        {"project_id": {"$in": [project_id, unrelated_project_id]}},
                        {"project_ids": {"$in": [project_id, unrelated_project_id]}},
                        {"contact_id": {"$in": [contact_id, unrelated_contact_id]}},
                    ]
                }
            )
            for doc in stale:
                if doc.get("file_path"):
                    Path(doc["file_path"]).unlink(missing_ok=True)

            screenshot = await mobile_artifacts.save_screenshot(
                db,
                image_base64=PNG_1X1,
                project_id=project_id,
                task_id="route-task",
                device_id="route-device",
                contact_id=contact_id,
                source="pytest_route",
                width=1,
                height=1,
                note="route linked screenshot",
            )
            from api.storage import get_object_storage

            access = await (await get_object_storage()).read_access(screenshot["storage_object_id"])
            screenshot_path = access.path
            assert screenshot_path is not None
            await mobile_artifacts.save_screenshot(
                db,
                image_base64=PNG_1X1,
                project_id=unrelated_project_id,
                task_id="other-task",
                device_id="other-device",
                contact_id=unrelated_contact_id,
                source="pytest_route",
            )
            operation = await mobile_artifacts.log_operation(
                db,
                operation_type="tap",
                project_id=project_id,
                task_id="route-task",
                device_id="route-device",
                contact_id=contact_id,
                action="tap",
                data={"x": 12, "y": 34},
                screenshot_id=screenshot["screenshot_id"],
            )
            await mobile_artifacts.log_operation(
                db,
                operation_type="tap",
                project_id=unrelated_project_id,
                task_id="other-task",
                device_id="other-device",
                contact_id=unrelated_contact_id,
            )
            await contact_profiles.upsert_profile(
                db,
                contact_id,
                {
                    "name": "Route Contact",
                    "platform": "wechat",
                    "device_id": "route-device",
                    "persona": {"summary": "route profile"},
                },
                project_id=project_id,
            )
            await contact_profiles.append_observation(
                db,
                contact_id,
                {"source": "pytest", "content": "route observation"},
                project_id=project_id,
            )
            await contact_profiles.upsert_profile(
                db,
                unrelated_contact_id,
                {
                    "name": "Other Contact",
                    "platform": "wechat",
                    "device_id": "other-device",
                    "persona": {"summary": "other profile"},
                },
                project_id=unrelated_project_id,
            )
            await auto_chat_sessions.upsert_session(
                db,
                {
                    "task_id": "route-session",
                    "device_id": "route-device",
                    "contact_id": contact_id,
                    "project_id": project_id,
                    "status": "running",
                    "last_message": "hello",
                },
            )
            await auto_chat_sessions.upsert_session(
                db,
                {
                    "task_id": "other-session",
                    "device_id": "other-device",
                    "contact_id": unrelated_contact_id,
                    "project_id": unrelated_project_id,
                    "status": "running",
                },
            )

            screenshots = await mobile_router.list_project_screenshots(
                project_id, limit=20
            )
            operations = await mobile_router.list_project_operations(
                project_id, limit=20
            )
            profiles = await mobile_router.list_project_profiles(project_id, limit=20)
            sessions = await mobile_router.list_project_auto_chat_sessions(
                project_id, limit=20
            )
            image_response = await mobile_router.get_mobile_screenshot_image(
                screenshot["screenshot_id"]
            )

            assert screenshots["total"] == 1
            assert screenshots["screenshots"][0]["screenshot_id"] == screenshot[
                "screenshot_id"
            ]
            assert screenshots["screenshots"][0]["url"] == (
                f"/api/v1/mobile/screenshots/{screenshot['screenshot_id']}/image"
            )
            assert operations["total"] == 1
            assert operations["operations"][0]["operation_id"] == operation[
                "operation_id"
            ]
            assert operations["operations"][0]["screenshot_id"] == screenshot[
                "screenshot_id"
            ]
            assert profiles["total"] == 1
            assert profiles["profiles"][0]["contact_id"] == contact_id
            assert project_id in profiles["profiles"][0].get("project_ids", [])
            assert profiles["profiles"][0]["observations"][-1]["project_id"] == project_id
            assert sessions["total"] == 1
            assert sessions["sessions"][0]["task_id"] == "route-session"
            assert isinstance(image_response, FileResponse)
            assert image_response.media_type == "image/png"
            assert Path(image_response.path) == screenshot_path
            assert screenshot_path.exists()
        finally:
            stale = await mobile_artifacts.list_screenshots(
                db, project_id=project_id, limit=100
            )
            stale += await mobile_artifacts.list_screenshots(
                db, project_id=unrelated_project_id, limit=100
            )
            await db[MOBILE_SCREENSHOTS_COLLECTION].delete_many(
                {"project_id": {"$in": [project_id, unrelated_project_id]}}
            )
            await db[MOBILE_OPERATION_LOGS_COLLECTION].delete_many(
                {"project_id": {"$in": [project_id, unrelated_project_id]}}
            )
            await db[STORAGE_OBJECTS_COLLECTION].delete_many(
                {"project_id": {"$in": [project_id, unrelated_project_id]}}
            )
            await db[AUTO_CHAT_SESSIONS_COLLECTION].delete_many(
                {"project_id": {"$in": [project_id, unrelated_project_id]}}
            )
            await db[CONTACT_PROFILES_COLLECTION].delete_many(
                {
                    "$or": [
                        {"project_id": {"$in": [project_id, unrelated_project_id]}},
                        {"project_ids": {"$in": [project_id, unrelated_project_id]}},
                        {"contact_id": {"$in": [contact_id, unrelated_contact_id]}},
                    ]
                }
            )
            close_mongo()

    asyncio.run(run())


def test_project_delete_cleans_mobile_artifacts_and_project_profile_refs(
    tmp_path: Path, monkeypatch
) -> None:
    async def run() -> None:
        from api.dao import (
            auto_chat_sessions,
            contact_profiles,
            mobile_artifacts,
            projects as projects_dao,
        )
        from api.db.collections import (
            AUTO_CHAT_SESSIONS_COLLECTION,
            CONTACT_PROFILES_COLLECTION,
            MOBILE_OPERATION_LOGS_COLLECTION,
            MOBILE_SCREENSHOTS_COLLECTION,
            PROJECTS_COLLECTION,
            STORAGE_OBJECTS_COLLECTION,
        )
        from api.db.mongodb import close_mongo, get_db, init_mongo
        from api.routers import projects as projects_router

        monkeypatch.setenv("MOBILE_SCREENSHOT_DIR", str(tmp_path))
        monkeypatch.setenv("OBJECT_STORAGE_LOCAL_ROOT", str(tmp_path / "objects"))
        other_project_id = "__pytest_mobile_delete_other_project__"
        project_id = ""
        project_oid = None
        screenshot_path: Path | None = None

        init_mongo()
        db = get_db()
        await mobile_artifacts.ensure_indexes(db)
        await contact_profiles.ensure_indexes(db)
        await auto_chat_sessions.ensure_indexes(db)

        try:
            project = await projects_dao.create_project(
                db,
                name="__pytest_mobile_delete_project__",
                description="mobile delete cascade",
            )
            project_id = str(project["_id"])
            project_oid = project["_id"]

            screenshot = await mobile_artifacts.save_screenshot(
                db,
                image_base64=PNG_1X1,
                project_id=project_id,
                task_id="task-delete",
                device_id="device-delete",
                contact_id="wechat:delete",
                source="pytest_delete",
            )
            from api.storage import get_object_storage

            access = await (await get_object_storage()).read_access(screenshot["storage_object_id"])
            screenshot_path = access.path
            assert screenshot_path is not None
            assert screenshot_path.exists()

            await mobile_artifacts.log_operation(
                db,
                operation_type="tap",
                project_id=project_id,
                task_id="task-delete",
                device_id="device-delete",
                contact_id="wechat:delete",
                screenshot_id=screenshot["screenshot_id"],
            )
            await auto_chat_sessions.upsert_session(
                db,
                {
                    "task_id": "auto-delete",
                    "device_id": "device-delete",
                    "contact_id": "wechat:delete",
                    "project_id": project_id,
                    "status": "running",
                },
            )
            await contact_profiles.upsert_profile(
                db,
                "wechat:delete-only",
                {"name": "Delete Only", "platform": "wechat", "persona": {}},
                project_id=project_id,
            )
            await contact_profiles.upsert_profile(
                db,
                "wechat:shared",
                {"name": "Shared", "platform": "wechat", "persona": {}},
                project_id=other_project_id,
            )
            await contact_profiles.merge_persona(
                db,
                "wechat:shared",
                {"summary": "also belongs to deleted project"},
                project_id=project_id,
            )
            await contact_profiles.append_observation(
                db,
                "wechat:shared",
                {"source": "pytest", "content": "delete project observation"},
                project_id=project_id,
            )

            result = await projects_router.delete_project(project_id)
            assert result["ok"] is True
            assert result["mobile_artifacts"]["screenshots_deleted"] == 1
            assert result["mobile_artifacts"]["operations_deleted"] == 1
            assert result["contact_profiles"]["profiles_deleted"] == 1
            assert not screenshot_path.exists()
            assert await projects_dao.get_project(db, project_id) is None
            assert await mobile_artifacts.list_screenshots(db, project_id=project_id) == []
            assert await mobile_artifacts.list_operations(db, project_id=project_id) == []
            assert await auto_chat_sessions.list_sessions(db, project_id=project_id) == []
            assert await contact_profiles.get_profile(db, "wechat:delete-only") is None

            shared = await contact_profiles.get_profile(db, "wechat:shared")
            assert shared is not None
            assert shared.get("project_id") == other_project_id
            assert project_id not in (shared.get("project_ids") or [])
            assert other_project_id in (shared.get("project_ids") or [])
            assert all(
                obs.get("project_id") != project_id
                for obs in (shared.get("observations") or [])
            )
        finally:
            if project_oid:
                await db[PROJECTS_COLLECTION].delete_many({"_id": project_oid})
            if project_id:
                await db[MOBILE_SCREENSHOTS_COLLECTION].delete_many(
                    {"project_id": project_id}
                )
                await db[MOBILE_OPERATION_LOGS_COLLECTION].delete_many(
                    {"project_id": project_id}
                )
                await db[AUTO_CHAT_SESSIONS_COLLECTION].delete_many(
                    {"project_id": project_id}
                )
                await db[CONTACT_PROFILES_COLLECTION].delete_many(
                    {
                        "$or": [
                            {"project_id": project_id},
                            {"project_ids": project_id},
                            {"contact_id": {"$in": ["wechat:delete-only", "wechat:shared"]}},
                        ]
                    }
                )
                await db[STORAGE_OBJECTS_COLLECTION].delete_many({"project_id": project_id})
            if screenshot_path:
                screenshot_path.unlink(missing_ok=True)
            close_mongo()

    asyncio.run(run())
