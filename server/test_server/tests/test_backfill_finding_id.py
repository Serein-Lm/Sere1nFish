"""
一次性脚本：为历史画像数据补充 finding_id

XHS 画像：通过 xhs_user_id 从 findings 表反查 finding_id，回写到 xhs_profiles
抖音画像：通过 sec_uid + url 从 findings 表反查 finding_id，回写到 douyin_profiles

用法：
    python -m test_server.tests.test_backfill_finding_id
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from Sere1nGraph.graph.config.loader import load_config
from api.db.mongodb import init_mongo, get_db
from api.db.collections import (
    XHS_PROFILES_COLLECTION,
    DOUYIN_PROFILES_COLLECTION,
    FINDINGS_COLLECTION,
)


async def backfill_xhs():
    """为 xhs_profiles 补充 finding_id"""
    db = get_db()

    # 找所有没有 finding_id 的 XHS 画像
    cursor = db[XHS_PROFILES_COLLECTION].find(
        {"$or": [{"finding_id": {"$exists": False}}, {"finding_id": None}]},
        {"user_id": 1, "project_id": 1, "nickname": 1},
    )
    profiles = await cursor.to_list(1000)
    print(f"[XHS] 需要补充 finding_id 的画像: {len(profiles)}")

    updated = 0
    not_found = 0
    for p in profiles:
        uid = p.get("user_id")
        pid = p.get("project_id")
        if not uid:
            continue

        # 从 findings 表按 xhs_user_id 反查
        finding = await db[FINDINGS_COLLECTION].find_one(
            {"xhs_user_id": uid, "source": "xhs", "project_id": pid},
            {"finding_id": 1, "_id": 0},
            sort=[("attention_score", -1)],
        )

        if finding and finding.get("finding_id"):
            fid = finding["finding_id"]
            await db[XHS_PROFILES_COLLECTION].update_one(
                {"_id": p["_id"]},
                {"$set": {"finding_id": fid}},
            )
            updated += 1
            print(f"  ✓ {p.get('nickname', uid)} → {fid}")
        else:
            not_found += 1
            print(f"  ✗ {p.get('nickname', uid)} — 无关联 finding")

    print(f"[XHS] 完成: 更新 {updated}, 无关联 {not_found}")
    return updated, not_found


async def backfill_douyin():
    """为 douyin_profiles 补充 finding_id"""
    db = get_db()

    cursor = db[DOUYIN_PROFILES_COLLECTION].find(
        {"$or": [{"finding_id": {"$exists": False}}, {"finding_id": None}]},
        {"sec_uid": 1, "project_id": 1, "nickname": 1},
    )
    profiles = await cursor.to_list(1000)
    print(f"\n[抖音] 需要补充 finding_id 的画像: {len(profiles)}")

    updated = 0
    not_found = 0
    for p in profiles:
        sec_uid = p.get("sec_uid")
        pid = p.get("project_id")
        if not sec_uid:
            continue

        # 从 findings 表按 douyin_sec_uid 或 url 反查
        finding = await db[FINDINGS_COLLECTION].find_one(
            {"douyin_sec_uid": sec_uid, "source": "douyin", "project_id": pid},
            {"finding_id": 1, "_id": 0},
            sort=[("attention_score", -1)],
        )

        if not finding:
            # fallback: 通过 URL 匹配
            finding = await db[FINDINGS_COLLECTION].find_one(
                {"source": "douyin", "project_id": pid, "url": {"$regex": sec_uid}},
                {"finding_id": 1, "_id": 0},
                sort=[("attention_score", -1)],
            )

        if finding and finding.get("finding_id"):
            fid = finding["finding_id"]
            await db[DOUYIN_PROFILES_COLLECTION].update_one(
                {"_id": p["_id"]},
                {"$set": {"finding_id": fid}},
            )
            updated += 1
            print(f"  ✓ {p.get('nickname', sec_uid)} → {fid}")
        else:
            not_found += 1
            print(f"  ✗ {p.get('nickname', sec_uid)} — 无关联 finding")

    print(f"[抖音] 完成: 更新 {updated}, 无关联 {not_found}")
    return updated, not_found


async def show_stats():
    """显示当前 finding_id 覆盖情况"""
    db = get_db()

    xhs_total = await db[XHS_PROFILES_COLLECTION].count_documents({})
    xhs_with_fid = await db[XHS_PROFILES_COLLECTION].count_documents(
        {"finding_id": {"$exists": True, "$ne": None}}
    )

    dy_total = await db[DOUYIN_PROFILES_COLLECTION].count_documents({})
    dy_with_fid = await db[DOUYIN_PROFILES_COLLECTION].count_documents(
        {"finding_id": {"$exists": True, "$ne": None}}
    )

    print("\n" + "=" * 50)
    print("finding_id 覆盖统计")
    print("=" * 50)
    print(f"XHS 画像:  {xhs_with_fid}/{xhs_total} ({xhs_with_fid/xhs_total*100:.0f}%)" if xhs_total else "XHS 画像:  0/0")
    print(f"抖音画像:  {dy_with_fid}/{dy_total} ({dy_with_fid/dy_total*100:.0f}%)" if dy_total else "抖音画像:  0/0")


async def main():
    config_path = _root / "config.json"
    app_config = load_config(str(config_path) if config_path.exists() else None)
    init_mongo(app_config)

    print("=" * 50)
    print("历史画像 finding_id 补充脚本")
    print("=" * 50)

    await show_stats()

    print("\n开始补充...")
    xhs_updated, xhs_missing = await backfill_xhs()
    dy_updated, dy_missing = await backfill_douyin()

    await show_stats()

    print(f"\n总计: XHS 更新 {xhs_updated} 条, 抖音更新 {dy_updated} 条")
    if xhs_missing or dy_missing:
        print(f"注意: XHS {xhs_missing} 条、抖音 {dy_missing} 条画像无关联 finding（可能是旧数据或 pipeline 未生成 finding）")


if __name__ == "__main__":
    asyncio.run(main())
