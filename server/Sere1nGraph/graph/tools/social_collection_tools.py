"""Bounded task-creation and result tools for social place collection."""

from __future__ import annotations

import hashlib
import json

from langchain.tools import tool

from .builtin import _run_coro_sync


_SUPPORTED_PLATFORMS = {"meituan", "douyin"}


def _split_values(value: str, *, lowercase: bool = False) -> list[str]:
    normalized = str(value or "").replace("，", ",").replace("\n", ",")
    values = [item.strip() for item in normalized.split(",") if item.strip()]
    if lowercase:
        values = [item.lower() for item in values]
    return list(dict.fromkeys(values))


def _current_request_context() -> tuple[str, str, str]:
    from api.services.artifact_context import get_artifact_context

    context = get_artifact_context()
    if context is None:
        return "ai_hub", "", ""
    return context.owner or "ai_hub", context.conversation_id, context.project_id


@tool(
    "create_social_place_collection",
    description=(
        "创建一个手机社交地点图片采集 Job。适用于要求到美团/抖音搜索地点、进入评价或评论、"
        "审核公开图片并归档截图证据的任务。任务异步排队且不会抢占设备；返回 job_id/task_id 后"
        "立即停止重复创建。必须提供 project_id、place_name、device_id；platforms 为逗号分隔的"
        "meituan,douyin；keywords 可为空或用逗号分隔。创建前应调用 list_mobile_devices 确认设备。"
    ),
)
def create_social_place_collection(
    project_id: str,
    place_name: str,
    device_id: str,
    platforms: str = "meituan,douyin",
    collection_goal: str = "收集能够辨认地点环境、设施和现场情况的公开用户图片",
    keywords: str = "",
    max_results_per_platform: int = 6,
    max_images_per_result: int = 8,
) -> str:
    owner, conversation_id, context_project_id = _current_request_context()
    project_id = str(project_id or context_project_id or "").strip()
    place_name = str(place_name or "").strip()
    device_id = str(device_id or "").strip()
    if not project_id or not place_name or not device_id:
        return "缺少 project_id、place_name 或 device_id；请先确认项目和在线设备。"
    platform_values = _split_values(platforms, lowercase=True)
    unsupported = sorted(set(platform_values) - _SUPPORTED_PLATFORMS)
    if unsupported or not platform_values:
        return "platforms 仅支持 meituan、douyin。"
    keyword_values = _split_values(keywords)
    identity = json.dumps(
        {
            "owner": owner,
            "conversation_id": conversation_id,
            "project_id": project_id,
            "place_name": place_name,
            "device_id": device_id,
            "platforms": platform_values,
            "keywords": keyword_values,
            "goal": collection_goal,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    request_key = "hub:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]

    async def _create():
        from api.db.mongodb import get_db
        from api.models.social_collection import SocialCollectionRequest
        from api.services.social_collection import create_social_collection_job

        request = SocialCollectionRequest(
            project_id=project_id,
            place_name=place_name,
            device_id=device_id,
            platforms=platform_values,
            keywords=keyword_values,
            collection_goal=str(collection_goal or "").strip(),
            max_results_per_platform=max_results_per_platform,
            max_images_per_result=max_images_per_result,
            request_key=request_key,
        )
        return await create_social_collection_job(
            get_db(),
            request,
            requested_by=owner,
            start=True,
        )

    try:
        job = _run_coro_sync(_create())
    except Exception as exc:  # noqa: BLE001
        return f"创建社交地点图片采集失败：{exc}"
    return json.dumps(
        {
            "ok": True,
            "job_id": job.get("job_id"),
            "task_id": job.get("task_id") or job.get("parent_task_id"),
            "status": job.get("status"),
            "place_name": job.get("place_name"),
            "platforms": job.get("platforms"),
            "device_id": job.get("device_id"),
            "reused": bool(job.get("reused")),
            "message": "任务已进入设备租约队列；这不代表采集已经完成。",
        },
        ensure_ascii=False,
        default=str,
    )


@tool(
    "get_social_collection_job",
    description="按 job_id 查询社交地点图片采集的真实状态、平台进度、失败原因和媒体数量。",
)
def get_social_collection_job(job_id: str) -> str:
    job_id = str(job_id or "").strip()
    if not job_id:
        return "请提供 job_id。"

    async def _load():
        from api.dao import social_collection as social_dao
        from api.db.mongodb import get_db

        return await social_dao.get_job(get_db(), job_id)

    try:
        job = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"查询社交采集 Job 失败：{exc}"
    if not job:
        return f"未找到社交采集 Job：{job_id}"
    return json.dumps(job, ensure_ascii=False, indent=2, default=str)


@tool(
    "list_social_collection_media",
    description=(
        "按 job_id 读取已归档的社交地点图片证据。返回地点、平台、图片说明、评价上下文、"
        "图片查看 API 和完整上下文截图引用；只读取，不创建新任务。"
    ),
)
def list_social_collection_media(job_id: str, limit: int = 20) -> str:
    job_id = str(job_id or "").strip()
    if not job_id:
        return "请提供 job_id。"

    async def _load():
        from api.dao import social_collection as social_dao
        from api.db.mongodb import get_db

        items, total = await social_dao.list_media_evidence(
            get_db(),
            job_id=job_id,
            limit=max(1, min(int(limit or 20), 50)),
        )
        return {"job_id": job_id, "total": total, "items": items}

    try:
        payload = _run_coro_sync(_load())
    except Exception as exc:  # noqa: BLE001
        return f"读取社交图片证据失败：{exc}"
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


SOCIAL_COLLECTION_TOOLS = [
    create_social_place_collection,
    get_social_collection_job,
    list_social_collection_media,
]
