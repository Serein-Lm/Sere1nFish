"""
项目数据分析聚合服务（只读）。

把项目综合看板的聚合逻辑收敛在此，供 HTTP router 与 AI 中枢分析工具复用，
避免同一套聚合在多处重复。所有读取走既有 DAO/collection，不写库、不触发任务。
"""
from __future__ import annotations

import asyncio
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import findings as findings_dao
from api.db.collections import (
    TASKS_COLLECTION,
    XHS_NOTES_COLLECTION,
    XHS_PROFILES_COLLECTION,
    WEB_TAGS_COLLECTION,
    DOUYIN_SEARCH_RESULTS_COLLECTION,
    DOUYIN_TAGGED_RESULTS_COLLECTION,
    DOUYIN_PROFILES_COLLECTION,
    COPYWRITINGS_COLLECTION,
    CONTACT_PROFILES_COLLECTION,
    MOBILE_PROFILE_OBSERVATIONS_COLLECTION,
    URL_SCAN_RESULTS_COLLECTION,
    FINDINGS_COLLECTION,
)


async def resolve_project_dashboard(
    db: AsyncIOMotorDatabase, project_id: str
) -> dict[str, Any]:
    """
    项目综合看板聚合 — 一次并发拿到所有看板数据。

    聚合：findings 统计 + 任务统计 + 各数据源计数 + 高分 Top10 + token 消耗。
    """
    tasks_agg = [
        {"$match": {"project_id": project_id}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    pid = {"project_id": project_id}
    try:
        web_pid = {"project_id": {"$in": [project_id, ObjectId(project_id)]}}
    except Exception:
        web_pid = pid

    (
        findings_summary,
        tasks_status_docs,
        c_xhs_notes,
        c_xhs_profiles,
        c_web,
        c_dy_search,
        c_dy_tagged,
        c_dy_profiles,
        c_cw,
        c_mobile_profiles,
        c_mobile_profile_observations,
        top_findings,
        safe_count,
    ) = await asyncio.gather(
        findings_dao.get_findings_summary(db, project_id),
        db[TASKS_COLLECTION].aggregate(tasks_agg).to_list(100),
        db[XHS_NOTES_COLLECTION].count_documents(pid),
        db[XHS_PROFILES_COLLECTION].count_documents(pid),
        db[WEB_TAGS_COLLECTION].count_documents(web_pid),
        db[DOUYIN_SEARCH_RESULTS_COLLECTION].count_documents(pid),
        db[DOUYIN_TAGGED_RESULTS_COLLECTION].count_documents(pid),
        db[DOUYIN_PROFILES_COLLECTION].count_documents(pid),
        db[COPYWRITINGS_COLLECTION].count_documents(pid),
        db[CONTACT_PROFILES_COLLECTION].count_documents(
            {
                "$or": [
                    {"project_id": project_id},
                    {"project_ids": project_id},
                    {"project_links.project_id": project_id},
                ]
            }
        ),
        db[MOBILE_PROFILE_OBSERVATIONS_COLLECTION].count_documents(pid),
        db[FINDINGS_COLLECTION].find(
            pid,
            {"_id": 0, "finding_id": 1, "source": 1, "type": 1, "label": 1, "value": 1, "attention_score": 1},
        ).sort("attention_score", -1).limit(10).to_list(10),
        db[URL_SCAN_RESULTS_COLLECTION].count_documents(
            {"project_id": project_id, "success": True, "has_findings": False}
        ),
    )

    tasks_by_status = {doc["_id"]: doc["count"] for doc in tasks_status_docs}
    data_counts = {
        "xhs_notes": c_xhs_notes,
        "xhs_profiles": c_xhs_profiles,
        "web_tagging": c_web,
        "douyin_search": c_dy_search,
        "douyin_tagged": c_dy_tagged,
        "douyin_profiles": c_dy_profiles,
        "copywritings": c_cw,
        "mobile_profiles": c_mobile_profiles,
        "mobile_profile_observations": c_mobile_profile_observations,
    }

    token_stats: dict[str, Any] = {}
    try:
        from Sere1nGraph.graph.observability import get_global_tracker

        tracker = get_global_tracker()
        if getattr(tracker, "_db", None) is None:
            tracker.set_db(db)
        token_stats = await tracker.get_stats_async(project_id=project_id)
    except Exception:
        pass

    return {
        "project_id": project_id,
        "findings": findings_summary,
        "tasks": {"total": sum(tasks_by_status.values()), "by_status": tasks_by_status},
        "data_counts": data_counts,
        "top_findings": top_findings,
        "safe_count": safe_count,
        "token_usage": token_stats,
    }
