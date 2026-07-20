"""
统一外部资产情报 DAO（保留历史 ``fofa_assets`` 集合和 API 名称兼容）。

按稳定 asset_id 唯一索引 + upsert 增量入库。
同一 (project_id, host, ip, port) 映射到确定的 asset_id：
- 首次入库写入 created_at 与全部字段；
- 再次入库仅更新 updated_at 与变化字段，不重复插入。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import UpdateOne

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
    await coll.create_index([("project_id", 1), ("target_id", 1)])
    await coll.create_index([("project_id", 1), ("target_ids", 1)])
    await coll.create_index([("project_id", 1), ("is_alive", 1)])
    await coll.create_index([("project_id", 1), ("canonical_url", 1)], sparse=True)
    await coll.create_index("updated_at")


def _content_hash(asset: dict[str, Any]) -> str:
    probe = asset.get("probe") or {}
    meaningful = {
        key: asset.get(key)
        for key in (
            "host",
            "ip",
            "port",
            "protocol",
            "domain",
            "title",
            "link",
            "canonical_url",
            "cert_domain",
            "fingerprints",
            "is_alive",
        )
    }
    meaningful["probe"] = {
        key: probe.get(key)
        for key in ("is_alive", "status_code", "title")
    }
    raw = json.dumps(meaningful, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def upsert_assets_batch(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    root_domain: str,
    source_query: str,
    search_type: str,
    assets: list[dict[str, Any]],
    task_id: str = "",
    target_id: str = "",
) -> dict[str, Any]:
    """
    批量增量入库 FOFA 资产。

    Args:
        assets: fofa_tools.FofaAsset.as_dict() 列表
        search_type: "domain" 或 "cert"

    Returns:
        {"inserted": 新增数, "updated": 更新数, "total": 处理总数}
    """
    if not assets:
        return {
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "total": 0,
            "inserted_asset_ids": [],
            "changed_asset_ids": [],
        }

    coll = db[FOFA_ASSETS_COLLECTION]
    now = _now()
    prepared_by_id: dict[str, tuple[dict[str, Any], list[str], list[str]]] = {}
    for asset in assets:
        host = str(asset.get("host") or "").strip()
        ip = str(asset.get("ip") or "").strip()
        port = str(asset.get("port") or "").strip()
        if not (host or ip):
            continue

        asset_id = fofa_asset_id(project_id, host, ip, port)
        sources = sorted(
            {str(value).strip() for value in asset.get("sources") or [] if str(value).strip()}
        )
        if not sources:
            sources = [str(asset.get("source") or "fofa").strip() or "fofa"]
        source_queries = sorted(
            {
                str(value).strip()
                for value in asset.get("source_queries") or []
                if str(value).strip()
            }
        )
        set_fields: dict[str, Any] = {
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
            "canonical_url": asset.get("canonical_url") or asset.get("link", ""),
            "cert_domain": asset.get("cert_domain", ""),
            "fingerprints": list(asset.get("fingerprints") or []),
            "search_type": search_type,
            "source_query": source_query,
            "is_alive": asset.get("is_alive"),
            "probe": dict(asset.get("probe") or {}),
            "latest_task_id": task_id,
            "last_seen_at": now,
            "updated_at": now,
        }
        resolved_target_id = str(asset.get("target_id") or target_id or "").strip()
        if resolved_target_id:
            set_fields["target_id"] = resolved_target_id
        set_fields["content_hash"] = _content_hash(set_fields)
        previous = prepared_by_id.get(asset_id)
        if previous:
            previous_fields, previous_sources, previous_queries = previous
            for key, value in set_fields.items():
                if value not in (None, "", [], {}):
                    previous_fields[key] = value
            previous_sources[:] = list(dict.fromkeys([*previous_sources, *sources]))
            previous_queries[:] = list(dict.fromkeys([*previous_queries, *source_queries]))
            previous_fields["content_hash"] = _content_hash(previous_fields)
        else:
            prepared_by_id[asset_id] = (set_fields, sources, source_queries)

    prepared = [
        (asset_id, fields, sources, queries)
        for asset_id, (fields, sources, queries) in prepared_by_id.items()
    ]

    if not prepared:
        return {
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "total": 0,
            "inserted_asset_ids": [],
            "changed_asset_ids": [],
        }

    ids = [asset_id for asset_id, _, _, _ in prepared]
    existing_cursor = coll.find(
        {"asset_id": {"$in": ids}}, {"_id": 0, "asset_id": 1, "content_hash": 1}
    )
    existing = {
        str(doc.get("asset_id") or ""): str(doc.get("content_hash") or "")
        async for doc in existing_cursor
    }
    inserted_ids: list[str] = []
    changed_ids: list[str] = []
    unchanged = 0
    operations: list[UpdateOne] = []

    for asset_id, set_fields, sources, source_queries in prepared:
        previous_hash = existing.get(asset_id)
        if previous_hash is None:
            inserted_ids.append(asset_id)
            changed_ids.append(asset_id)
        elif previous_hash != set_fields["content_hash"]:
            changed_ids.append(asset_id)
        else:
            unchanged += 1

        update: dict[str, Any] = {
            "$set": set_fields,
            "$setOnInsert": {"created_at": now},
        }
        additions: dict[str, Any] = {}
        if sources:
            additions["sources"] = {"$each": sources}
        if source_queries:
            additions["source_queries"] = {"$each": source_queries}
        if task_id:
            additions["task_ids"] = task_id
        resolved_target_id = str(set_fields.get("target_id") or "")
        if resolved_target_id:
            additions["target_ids"] = resolved_target_id
        if additions:
            update["$addToSet"] = additions
        operations.append(UpdateOne({"asset_id": asset_id}, update, upsert=True))

    await coll.bulk_write(operations, ordered=False)
    inserted_set = set(inserted_ids)
    updated_ids = [asset_id for asset_id in changed_ids if asset_id not in inserted_set]
    return {
        "inserted": len(inserted_ids),
        "updated": len(updated_ids),
        "unchanged": unchanged,
        "total": len(prepared),
        "inserted_asset_ids": inserted_ids,
        "changed_asset_ids": changed_ids,
    }


async def query_assets(
    db: AsyncIOMotorDatabase,
    project_id: str,
    root_domain: str = "",
    target_id: str = "",
    limit: int = 500,
    skip: int = 0,
) -> list[dict[str, Any]]:
    """查询项目下的 FOFA 资产。"""
    query: dict[str, Any] = {"project_id": project_id}
    if root_domain:
        query["root_domain"] = root_domain
    if target_id:
        query["$or"] = [{"target_ids": target_id}, {"target_id": target_id}]
    cursor = (
        db[FOFA_ASSETS_COLLECTION]
        .find(query, {"_id": 0})
        .sort("updated_at", -1)
        .skip(max(0, int(skip or 0)))
        .limit(max(1, int(limit or 500)))
    )
    return [doc async for doc in cursor]


async def count_assets(
    db: AsyncIOMotorDatabase,
    project_id: str,
    *,
    root_domain: str = "",
    target_id: str = "",
) -> int:
    query: dict[str, Any] = {"project_id": project_id}
    if root_domain:
        query["root_domain"] = root_domain
    if target_id:
        query["$or"] = [{"target_ids": target_id}, {"target_id": target_id}]
    return await db[FOFA_ASSETS_COLLECTION].count_documents(query)


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
