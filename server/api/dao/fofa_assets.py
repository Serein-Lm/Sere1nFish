"""
FOFA 资产 DAO

按稳定 asset_id 唯一索引 + upsert 增量入库。
同一 (project_id, host, ip, port) 映射到确定的 asset_id：
- 首次入库写入 created_at 与全部字段；
- 再次入库仅更新 updated_at 与变化字段，不重复插入。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import FOFA_ASSETS_COLLECTION


def _now() -> datetime:
    return datetime.now(timezone.utc)


def fofa_asset_id(project_id: str, host: str, ip: str, port: str) -> str:
    """一个 (project, host, ip, port) 对应确定的资产 id。"""
    raw = f"fofa:{project_id}:{host}:{ip}:{port}".encode("utf-8")
    return "fa_" + hashlib.sha1(raw).hexdigest()[:20]


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """幂等建立索引。"""
    coll = db[FOFA_ASSETS_COLLECTION]
    await coll.create_index("asset_id", unique=True)
    await coll.create_index([("project_id", 1), ("root_domain", 1)])
    await coll.create_index("updated_at")


async def upsert_assets_batch(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    root_domain: str,
    source_query: str,
    search_type: str,
    assets: list[dict[str, Any]],
    task_id: str = "",
) -> dict[str, int]:
    """
    批量增量入库 FOFA 资产。

    Args:
        assets: fofa_tools.FofaAsset.as_dict() 列表
        search_type: "domain" 或 "cert"

    Returns:
        {"inserted": 新增数, "updated": 更新数, "total": 处理总数}
    """
    if not assets:
        return {"inserted": 0, "updated": 0, "total": 0}

    coll = db[FOFA_ASSETS_COLLECTION]
    now = _now()
    inserted = 0
    updated = 0

    for asset in assets:
        host = str(asset.get("host") or "").strip()
        ip = str(asset.get("ip") or "").strip()
        port = str(asset.get("port") or "").strip()
        if not (host or ip):
            continue

        asset_id = fofa_asset_id(project_id, host, ip, port)
        set_fields = {
            "asset_id": asset_id,
            "project_id": project_id,
            "root_domain": root_domain,
            "host": host,
            "ip": ip,
            "port": port,
            "protocol": asset.get("protocol", ""),
            "domain": asset.get("domain", ""),
            "title": asset.get("title", ""),
            "link": asset.get("link", ""),
            "cert_domain": asset.get("cert_domain", ""),
            "search_type": search_type,
            "source_query": source_query,
            "latest_task_id": task_id,
            "updated_at": now,
        }
        update: dict[str, Any] = {
            "$set": set_fields,
            "$setOnInsert": {"created_at": now},
        }
        if task_id:
            update["$addToSet"] = {"task_ids": task_id}

        result = await coll.update_one({"asset_id": asset_id}, update, upsert=True)
        if result.upserted_id is not None:
            inserted += 1
        elif result.modified_count:
            updated += 1

    return {"inserted": inserted, "updated": updated, "total": inserted + updated}


async def query_assets(
    db: AsyncIOMotorDatabase,
    project_id: str,
    root_domain: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    """查询项目下的 FOFA 资产。"""
    query: dict[str, Any] = {"project_id": project_id}
    if root_domain:
        query["root_domain"] = root_domain
    cursor = db[FOFA_ASSETS_COLLECTION].find(query, {"_id": 0}).sort("updated_at", -1).limit(limit)
    return [doc async for doc in cursor]


async def query_assets_by_root_domain(
    db: AsyncIOMotorDatabase,
    root_domain: str,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """跨项目按根域名查询 FOFA 资产（供上下文聚合层在无项目上下文时解析公司资产）。"""
    if not root_domain:
        return []
    cursor = (
        db[FOFA_ASSETS_COLLECTION]
        .find({"root_domain": root_domain.strip()}, {"_id": 0})
        .sort("updated_at", -1)
        .limit(limit)
    )
    return [doc async for doc in cursor]


async def list_asset_ids(db: AsyncIOMotorDatabase, project_id: str) -> set[str]:
    """返回项目已入库的 asset_id 集合，供增量判断（跳过已存在资产的重复处理）。"""
    cursor = db[FOFA_ASSETS_COLLECTION].find(
        {"project_id": project_id}, {"_id": 0, "asset_id": 1}
    )
    return {doc["asset_id"] async for doc in cursor if doc.get("asset_id")}
