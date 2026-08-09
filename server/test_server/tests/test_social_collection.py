"""Social collection contracts without touching a real mobile device."""

from __future__ import annotations

import asyncio
import base64
import io
from datetime import datetime, timezone

import pytest
from PIL import Image
from pydantic import ValidationError


def _request(**overrides):
    from api.models.social_collection import SocialCollectionRequest

    values = {
        "project_id": "project-1",
        "place_name": "西湖文化广场",
        "device_id": "device-1",
        "platforms": ["meituan", "douyin"],
    }
    values.update(overrides)
    return SocialCollectionRequest(**values)


def _png_base64(width: int = 200, height: int = 100, color=(40, 120, 200)) -> str:
    image = Image.new("RGB", (width, height), color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def test_compile_plan_is_registry_driven_and_has_no_side_effects() -> None:
    from api.services.social_collection import compile_social_collection_plan

    plan = compile_social_collection_plan(_request())

    assert plan["will_operate_device"] is False
    assert plan["execution"] == "sequential_on_one_device"
    assert [item["platform"] for item in plan["tasks"]] == ["meituan", "douyin"]
    assert {
        item["task_definition"]["progress_source"] for item in plan["tasks"]
    } == {"social_photos_meituan", "social_photos_douyin"}
    for item in plan["tasks"]:
        definition = item["task_definition"]
        assert definition["candidate_policy"] == "social_place_media"
        assert definition["detail_capture_strategy"] == "social_place_gallery"
        assert definition["score_policy"] == "raw"
        assert definition["extract_contact_findings"] is False
        assert definition["resolve_target_context"] is False
        assert definition["require_persist_success"] is True
        assert definition["use_target_keyword_library"] is False


def test_request_rejects_blank_identifiers_and_oversized_keywords() -> None:
    with pytest.raises(ValidationError):
        _request(project_id="   ")
    with pytest.raises(ValidationError):
        _request(keywords=["x" * 121])


def test_preview_endpoint_only_compiles_plan(monkeypatch) -> None:
    from api.models.social_collection import SocialCollectionPreviewRequest
    from api.routers.social_collection import preview_job

    request = SocialCollectionPreviewRequest(**_request().model_dump())
    result = asyncio.run(preview_job(request))
    assert result["will_operate_device"] is False
    assert result["task_count"] == 2


def test_create_job_persists_definitions_without_running_mobile(monkeypatch) -> None:
    from api.services.social_collection import service

    class Collection:
        def __init__(self):
            self.items = []

        async def insert_one(self, document):
            self.items.append(dict(document))

    class DB:
        def __init__(self):
            self.collection = Collection()

        def __getitem__(self, _name):
            return self.collection

    db = DB()
    definitions = []

    async def validate(*_args, **_kwargs):
        return None

    async def find_active(*_args, **_kwargs):
        return None

    async def create_definition(_db, payload):
        definitions.append(dict(payload))
        return {**payload, "task_def_id": f"def-{len(definitions)}"}

    async def create_job(_db, **kwargs):
        return {
            "job_id": kwargs["job_id"],
            "parent_task_id": kwargs["parent_task_id"],
            "status": "pending",
            "place_name": kwargs["payload"]["place_name"],
            "platforms": kwargs["payload"]["platforms"],
            "device_id": kwargs["payload"]["device_id"],
        }

    monkeypatch.setattr(service, "_validate_runtime_references", validate)
    monkeypatch.setattr(service.social_dao, "find_active_job_by_request_key", find_active)
    monkeypatch.setattr(service.mobile_collect_dao, "create_task_def", create_definition)
    monkeypatch.setattr(service.social_dao, "create_job", create_job)

    result = asyncio.run(
        service.create_social_collection_job(
            db,
            _request(),
            requested_by="tester",
            start=False,
        )
    )

    assert result["status"] == "pending"
    assert len(definitions) == 2
    assert len(db.collection.items) == 1
    assert db.collection.items[0]["task_type"] == "social_media_collect"
    assert all(item["social_collection_job_id"] == result["job_id"] for item in definitions)


def test_create_job_rolls_back_definitions_and_parent_on_task_insert_error(
    monkeypatch,
) -> None:
    from api.services.social_collection import service

    class Collection:
        async def insert_one(self, _document):
            raise RuntimeError("task insert failed")

        async def delete_one(self, _query):
            return None

    class DB:
        def __getitem__(self, _name):
            return Collection()

    deleted_definitions: list[str] = []
    deleted_jobs: list[str] = []

    async def validate(*_args, **_kwargs):
        return None

    async def find_active(*_args, **_kwargs):
        return None

    async def create_definition(_db, payload):
        return {**payload, "task_def_id": f"def-{payload['platform']}"}

    async def delete_definition(_db, task_def_id):
        deleted_definitions.append(task_def_id)
        return 1

    async def create_job(_db, **kwargs):
        return {"job_id": kwargs["job_id"], "status": "pending"}

    async def delete_job(_db, job_id):
        deleted_jobs.append(job_id)
        return 1

    monkeypatch.setattr(service, "_validate_runtime_references", validate)
    monkeypatch.setattr(service.social_dao, "find_active_job_by_request_key", find_active)
    monkeypatch.setattr(service.mobile_collect_dao, "create_task_def", create_definition)
    monkeypatch.setattr(service.mobile_collect_dao, "delete_task_def", delete_definition)
    monkeypatch.setattr(service.social_dao, "create_job", create_job)
    monkeypatch.setattr(service.social_dao, "delete_job", delete_job)

    with pytest.raises(RuntimeError, match="task insert failed"):
        asyncio.run(
            service.create_social_collection_job(
                DB(),
                _request(),
                requested_by="tester",
                start=False,
            )
        )

    assert len(deleted_definitions) == 2
    assert len(deleted_jobs) == 1


def test_agent_tool_preserves_keyword_case() -> None:
    from Sere1nGraph.graph.tools.social_collection_tools import _split_values

    assert _split_values("West Lake,西湖") == ["West Lake", "西湖"]
    assert _split_values("MEITUAN,DouYin", lowercase=True) == [
        "meituan",
        "douyin",
    ]


def test_screen_render_crop_preserves_expected_pixels() -> None:
    from api.services.social_collection.media import crop_screen_render

    content, width, height = crop_screen_render(
        _png_base64(width=200, height=100),
        [250, 200, 750, 800],
    )

    cropped = Image.open(io.BytesIO(content))
    assert (width, height) == (100, 60)
    assert cropped.size == (100, 60)
    assert cropped.getpixel((20, 20)) == (40, 120, 200)


def test_social_media_object_key_is_project_and_platform_scoped() -> None:
    from api.storage.keys import build_object_key

    key = build_object_key(
        prefix="sere1nfish/prod",
        kind="social_media_image",
        object_id="sme_example",
        extension="png",
        project_id="project-1",
        relative_path="douyin",
        created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )

    assert key == (
        "sere1nfish/prod/projects/project-1/collect/social/douyin/"
        "2026/08/09/sme_example.png"
    )


def test_raw_score_policy_does_not_require_contacts() -> None:
    from core.mobile.collect.score_policy import ScorePolicyRegistry

    assert ScorePolicyRegistry.resolve("raw").score(86, has_contacts=False) == 86
    assert (
        ScorePolicyRegistry.resolve("contact_weighted").score(
            86, has_contacts=False
        )
        < 86
    )


def test_partial_media_archive_marks_platform_partial() -> None:
    from api.services.social_collection.service import _platform_result_status

    assert _platform_result_status({"media": 3, "media_failed": 1}) == "partial"
    assert _platform_result_status({"media": 3, "media_failed": 0}) == "completed"


def test_social_candidate_policy_requires_visible_place_identity() -> None:
    from core.mobile.collect.candidate_policy import CandidatePolicyRegistry

    policy = CandidatePolicyRegistry.resolve("social_place_media")
    candidate = {
        "tap_bounds": [80, 180, 920, 420],
        "score": 82,
        "subject_match": 90,
        "content_kind": "place",
        "target_evidence": "西湖文化广场",
        "fields": {
            "content_title": "西湖文化广场",
            "place_name": "西湖文化广场",
            "location": "杭州",
        },
    }
    assert policy.review_detail(
        candidate,
        min_score=55,
        min_subject_match=70,
        target_name="西湖文化广场",
    ).accepted
    rejected = policy.review_detail(
        {
            **candidate,
            "target_evidence": "附近推荐",
            "fields": {"content_title": "武林广场", "place_name": "武林广场"},
        },
        min_score=55,
        min_subject_match=70,
        target_name="西湖文化广场",
    )
    assert rejected.accepted is False


def test_social_collection_intent_routes_to_collection_specialist() -> None:
    from Sere1nGraph.graph.workflow.hub import (
        _has_social_collection_intent,
        _social_collection_classifications,
    )

    result = _social_collection_classifications(
        "帮我收集西湖文化广场的照片，去美团搜索评价图片并保存"
    )
    assert result == [
        {
            "source": "collection",
            "query": "帮我收集西湖文化广场的照片，去美团搜索评价图片并保存",
            "requires_tools": True,
        }
    ]
    assert _has_social_collection_intent("去美团搜一下西湖图集，找到后归档")


def test_social_job_resume_skips_completed_platform_and_marks_timeout_partial(
    monkeypatch,
) -> None:
    from api.db import mongodb
    from api.services import mobile_collect_pipeline
    from api.services.social_collection import service

    job = {
        "job_id": "job-1",
        "place_name": "西湖文化广场",
        "requested_by": "tester",
        "platform_tasks": [
            {
                "platform": "meituan",
                "status": "completed",
                "task_def_id": "def-meituan",
                "result": {"media": 2},
            },
            {
                "platform": "douyin",
                "status": "pending",
                "task_def_id": "def-douyin",
            },
        ],
    }
    calls: list[str] = []
    statuses: list[tuple[str, str]] = []
    finished: dict = {}

    async def set_running(*_args, **_kwargs):
        return job

    async def update_status(_db, *, platform, status, **_kwargs):
        statuses.append((platform, status))

    async def run_definition(_db, *, task_def_id, **_kwargs):
        calls.append(task_def_id)
        return {"timed_out": True, "stopped": False, "media": 1}

    async def count_media(*_args, **_kwargs):
        return 3

    async def finish(_db, *, status, result, **_kwargs):
        finished.update({"status": status, "result": result})

    monkeypatch.setattr(mongodb, "get_db", lambda: object())
    monkeypatch.setattr(service.social_dao, "set_job_running", set_running)
    monkeypatch.setattr(service.social_dao, "update_platform_status", update_status)
    monkeypatch.setattr(service.social_dao, "count_job_media", count_media)
    monkeypatch.setattr(service.social_dao, "finish_job", finish)
    monkeypatch.setattr(
        mobile_collect_pipeline,
        "run_mobile_collect_definition",
        run_definition,
    )

    result = asyncio.run(
        service.execute_social_collection_job(
            "parent-1",
            "project-1",
            {"job_id": "job-1", "_requested_by": "tester"},
        )
    )

    assert calls == ["def-douyin"]
    assert statuses == [("douyin", "running"), ("douyin", "partial")]
    assert result["status"] == "partial"
    assert result["partial_platforms"] == ["douyin"]
    assert finished["status"] == "partial"


def test_unexpected_orchestration_failure_marks_job_terminal(monkeypatch) -> None:
    from api.db import mongodb
    from api.services.social_collection import service

    job = {
        "job_id": "job-orchestration-error",
        "place_name": "西湖文化广场",
        "progress": {"media_count": 2},
        "platform_tasks": [
            {
                "platform": "meituan",
                "status": "pending",
                "task_def_id": "def-meituan",
            }
        ],
    }
    finished: dict = {}

    async def set_running(*_args, **_kwargs):
        return job

    async def fail_status_update(*_args, **_kwargs):
        raise RuntimeError("progress write failed")

    async def fail_media_count(*_args, **_kwargs):
        raise RuntimeError("count unavailable")

    async def finish(_db, *, status, result, **_kwargs):
        finished.update({"status": status, "result": result})

    monkeypatch.setattr(mongodb, "get_db", lambda: object())
    monkeypatch.setattr(service.social_dao, "set_job_running", set_running)
    monkeypatch.setattr(
        service.social_dao,
        "update_platform_status",
        fail_status_update,
    )
    monkeypatch.setattr(service.social_dao, "count_job_media", fail_media_count)
    monkeypatch.setattr(service.social_dao, "finish_job", finish)

    with pytest.raises(RuntimeError, match="progress write failed"):
        asyncio.run(
            service.execute_social_collection_job(
                "parent-error",
                "project-1",
                {"job_id": "job-orchestration-error"},
            )
        )

    assert finished["status"] == "error"
    assert finished["result"]["media_count"] == 2
    assert finished["result"]["failures"] == [
        {"platform": "orchestration", "error": "progress write failed"}
    ]


def test_hub_catalog_exposes_bounded_social_collection_tools() -> None:
    from Sere1nGraph.graph.tools.catalog import get_hub_tool_catalog

    catalog = get_hub_tool_catalog()
    collection = next(
        item for item in catalog["agents"] if item["name"] == "collection"
    )
    assert "create_social_place_collection" in collection["tools"]
    assert "get_social_collection_job" in collection["tools"]
    assert "list_social_collection_media" in collection["tools"]
