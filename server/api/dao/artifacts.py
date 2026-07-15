"""
产物（Word 文档等）元信息 DAO。

产物文件由统一 StorageService 保存，MongoDB 存可检索元信息与稳定鉴权下载路径。
供 AI 中枢通用工具（generate_word_document）调用：先生成文件，再登记元信息，
返回稳定 artifact_id 与受登录鉴权的下载链接。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import ARTIFACTS_COLLECTION


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def artifacts_dir() -> Path:
    """产物文件落地目录（幂等创建）。"""
    configured = os.getenv("ARTIFACTS_DIR")
    root = Path(configured) if configured else Path.cwd() / "data" / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def download_url(artifact_id: str) -> str:
    """受登录鉴权的下载链接。"""
    return f"/api/v1/artifacts/{artifact_id}/download"


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """幂等建立索引。"""
    coll = db[ARTIFACTS_COLLECTION]
    await coll.create_index("artifact_id", unique=True)
    await coll.create_index("owner")
    await coll.create_index("kind")
    await coll.create_index("created_at")
    await coll.create_index([("owner", 1), ("created_at", -1)])
    await coll.create_index([("owner", 1), ("meta.conversation_id", 1), ("created_at", -1)])
    await coll.create_index([("owner", 1), ("meta.project_id", 1), ("created_at", -1)])


async def create_artifact(
    db: AsyncIOMotorDatabase,
    *,
    artifact_id: str,
    kind: str,
    title: str,
    filename: str,
    file_path: str = "",
    storage_object_id: str = "",
    size: int = 0,
    owner: str = "",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """登记产物元信息（按 artifact_id upsert）。"""
    now = _now()
    doc = {
        "artifact_id": artifact_id,
        "kind": kind,
        "title": title,
        "filename": filename,
        "file_path": file_path,
        "storage_object_id": storage_object_id,
        "size": size,
        "owner": owner,
        "meta": meta or {},
        "download_url": download_url(artifact_id),
        "created_at": now,
        "updated_at": now,
    }
    await db[ARTIFACTS_COLLECTION].update_one(
        {"artifact_id": artifact_id},
        {"$set": doc},
        upsert=True,
    )
    return doc


async def get_artifact(
    db: AsyncIOMotorDatabase, artifact_id: str
) -> dict[str, Any] | None:
    return await db[ARTIFACTS_COLLECTION].find_one(
        {"artifact_id": artifact_id}, {"_id": 0}
    )


async def list_artifacts(
    db: AsyncIOMotorDatabase,
    *,
    owner: str = "",
    kind: str = "",
    conversation_id: str = "",
    project_id: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if owner:
        query["owner"] = owner
    if kind:
        query["kind"] = kind
    if conversation_id:
        query["meta.conversation_id"] = conversation_id
    if project_id:
        query["meta.project_id"] = project_id
    cursor = (
        db[ARTIFACTS_COLLECTION]
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    return [doc async for doc in cursor]
