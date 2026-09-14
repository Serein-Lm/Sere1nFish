"""Authoritative global library read model, reusable by HTTP and analysis tools."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from api.dao import target_library as dao
from api.dao.targets import normalize_target_name
from api.services.bidding_ownership import BiddingOwnershipScope
from .identity import POLICY_VERSION, build_index
from .scans import scan_index, scan_summary, time_key

_cache: dict = {}
_lock = asyncio.Lock()


async def get_index(db, *, refresh: bool = False) -> dict:
    key = (id(db.client), db.name)
    async with _lock:
        cached = _cache.get(key)
        if cached and not refresh and time.monotonic() - cached[0] < 30:
            return cached[1]
        index = build_index(*(await dao.inventory(db)))
        await dao.save_identity_projection(db, index["targets"], index["mapping"], POLICY_VERSION)
        index["scans"] = scan_index(index, *(await dao.task_inventory(db)))
        index["generated_at"] = datetime.now(timezone.utc)
        _cache[key] = (time.monotonic(), index)
        return index


async def _enrich(db, index: dict, rows: list[dict]) -> list[dict]:
    scope = BiddingOwnershipScope(index["targets"])
    metrics, bids = await asyncio.gather(dao.archive_metrics(db, rows), dao.owned_bidding_counts(db, rows, scope))
    return [{**scan_summary(row, index["relations"].get(row["target_id"], []), index["scans"].get(row["target_id"], [])), **metrics.get(row["target_id"], {}), "bidding_count": bids.get(row["target_id"], 0)} for row in rows]


async def list_library(db, *, page: int = 1, page_size: int = 25, q: str = "", project_id: str = "", parent_id: str = "", refresh: bool = False) -> dict:
    index = await get_index(db, refresh=refresh)
    all_rows = list(index["rows"].values())
    rows = [row for row in all_rows if not project_id or any(item["project_id"] == project_id for item in row["projects"])]
    scoped_ids = {row["target_id"] for row in rows}
    query = normalize_target_name(q.strip())
    parent = index["mapping"].get(parent_id, parent_id)
    if query:
        rows = [row for row in rows if any(query in normalize_target_name(name) for name in row["aliases"])]
    elif parent:
        rows = [row for row in rows if row["parent_target_id"] == parent]
    else:
        rows = [row for row in rows if row["parent_target_id"] not in scoped_ids]
    def activity_key(row):
        run = (index["scans"].get(row["target_id"]) or [{}])[0]
        return time_key(run.get("started_at") or run.get("created_at")), row["target_name"]
    rows.sort(key=activity_key, reverse=True)
    total = len(rows)
    selected = rows[(page - 1) * page_size:page * page_size]
    return {"items": await _enrich(db, index, selected), "total": total, "page": page, "page_size": page_size,
            "target_count": len(all_rows), "original_target_count": len(index["mapping"]), "scoped_target_count": len(scoped_ids), "generated_at": index["generated_at"]}


async def get_target(db, target_id: str) -> tuple[dict, dict]:
    index = await get_index(db)
    anchor = index["mapping"].get(target_id, target_id)
    if anchor not in index["rows"]:
        raise ValueError("目标不存在")
    return index, index["rows"][anchor]


async def detail(db, target_id: str) -> dict:
    index, row = await get_target(db, target_id)
    return (await _enrich(db, index, [row]))[0]


async def history(db, target_id: str, *, kind: str, skip: int, limit: int, document_id: str = "") -> dict:
    index, row = await get_target(db, target_id)
    if kind == "scans":
        items = index["scans"].get(row["target_id"], [])
        return {"items": items[skip:skip + limit], "total": len(items), "skip": skip, "limit": limit}
    if kind == "versions":
        return await dao.list_versions(db, row["member_target_ids"], skip, limit, document_id)
    readers = {"documents": dao.list_documents, "mobile": dao.list_mobile}
    return await readers[kind](db, row["member_target_ids"], skip, limit)
