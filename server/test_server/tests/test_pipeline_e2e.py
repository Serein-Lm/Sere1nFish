"""
端到端 Pipeline 测试 — 真实调用，入库

测试 1: URL 扫描（跳过已有 findings 的 URL）+ 流式话术生成
测试 2: 补话术（查所有没有话术的 findings，补生成）
测试 3: XHS 画像（清空旧画像，重新生成全部）+ 流式话术

运行：python test_server/tests/test_pipeline_e2e.py
"""

import asyncio
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

LOG_FILE = _project_root / "logs" / "test_pipeline_e2e.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def setup_logging():
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = min(10240, hard)
    if soft < target:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(str(LOG_FILE), mode="w", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)
    return logging.getLogger("test_e2e")


logger = setup_logging()
PROJECT_ID = "69c8e71138c689f2defb7e0d"
URL_FILE = Path(__file__).parent / "url.txt"


def init_app():
    from Sere1nGraph.graph.config.loader import load_config
    from api.db.mongodb import init_mongo, get_db
    from Sere1nGraph.graph.observability import get_global_tracker
    app_config = load_config()
    init_mongo(app_config)
    db = get_db()
    tracker = get_global_tracker()
    tracker.set_db(db)
    logger.info(f"初始化完成 | project={PROJECT_ID}")
    return app_config, db


# ═══════════════════════════════════════════
# 测试 1: URL 扫描（跳过已有 findings 的 URL）
# ═══════════════════════════════════════════

async def test_url_scan(app_config, db):
    from api.services.url_scan_pipeline import UrlScanPipeline
    from Sere1nGraph.graph.observability import get_global_tracker

    logger.info("=" * 60)
    logger.info("  测试 1: URL 扫描")
    logger.info("=" * 60)

    if not URL_FILE.exists():
        logger.error(f"url.txt 不存在: {URL_FILE}")
        return
    url_content = URL_FILE.read_text(encoding="utf-8")
    pipeline = UrlScanPipeline(db, app_config)
    all_urls = pipeline.parse_url_file(url_content)
    if not all_urls:
        logger.error("url.txt 中没有有效 URL")
        return

    # 查已有 findings 的 URL，跳过
    existing_urls = set()
    cursor = db["web_tagging_results"].find(
        {"project_id": PROJECT_ID}, {"url": 1}
    )
    async for doc in cursor:
        existing_urls.add(doc.get("url", ""))
    # 也查 findings 集合
    cursor2 = db["findings"].find(
        {"project_id": PROJECT_ID, "source": "web_tagging"}, {"url": 1}
    )
    async for doc in cursor2:
        existing_urls.add(doc.get("url", ""))

    new_urls = [u for u in all_urls if u not in existing_urls]
    logger.info(f"总 URL: {len(all_urls)} | 已有 findings: {len(existing_urls)} | 待扫描: {len(new_urls)}")

    if not new_urls:
        logger.info("所有 URL 已扫描过，跳过")
        return

    # 只扫描新 URL
    new_url_content = "\n".join(new_urls)
    task_id = f"test_url_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    tracker = get_global_tracker()
    tracker.push_context(project_id=PROJECT_ID, task_id=task_id, phase="url_scan")

    t0 = time.time()
    try:
        result = await pipeline.run_pipeline(
            task_id=task_id, project_id=PROJECT_ID,
            url_content=new_url_content, probe_concurrency=20, min_attention_score=40,
        )
        logger.info(f"URL 扫描完成 ({time.time()-t0:.1f}s) | {result}")
    except Exception as e:
        logger.error(f"URL 扫描失败 ({time.time()-t0:.1f}s): {e}", exc_info=True)
    finally:
        tracker.pop_context()


# ═══════════════════════════════════════════
# 测试 2: 补话术（查没有话术的 findings）
# ═══════════════════════════════════════════

async def test_补话术(app_config, db):
    from api.services.url_scan_pipeline import UrlScanPipeline
    from api.dao import findings as findings_dao
    from Sere1nGraph.graph.observability import get_global_tracker

    logger.info("=" * 60)
    logger.info("  测试 2: 补话术")
    logger.info("=" * 60)

    # 查所有 findings（score >= 60）
    all_findings, total = await findings_dao.query_findings(
        db, project_id=PROJECT_ID, min_score=60, limit=500, sort="score_desc",
    )
    logger.info(f"项目下 score>=60 的 findings: {total} 个")

    # 查已有话术的 finding_id
    existing_cw = set()
    cursor = db["copywritings"].find({"project_id": PROJECT_ID}, {"finding_id": 1})
    async for doc in cursor:
        existing_cw.add(doc.get("finding_id", ""))
    # 也查 url_scan_copywritings
    cursor2 = db["url_scan_copywritings"].find({"project_id": PROJECT_ID}, {"finding_id": 1})
    async for doc in cursor2:
        existing_cw.add(doc.get("finding_id", ""))

    need_cw = [f for f in all_findings if f.get("finding_id") not in existing_cw]
    logger.info(f"已有话术: {len(existing_cw)} | 待生成: {len(need_cw)}")

    if not need_cw:
        logger.info("所有 findings 都已有话术，跳过")
        return

    from collections import defaultdict
    by_url = defaultdict(list)
    for f in need_cw:
        by_url[f.get("url", "")].append(f)

    task_id = f"test_cw_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    tracker = get_global_tracker()
    tracker.push_context(project_id=PROJECT_ID, task_id=task_id, phase="copywriting")

    pipeline = UrlScanPipeline(db, app_config)
    success, fail = 0, 0
    t0 = time.time()
    NUM_CW_WORKERS = 5  # 补数据用高并发

    cw_queue: asyncio.Queue = asyncio.Queue()
    for f in need_cw:
        cw_queue.put_nowait(f)

    async def _cw_worker(wid: int):
        nonlocal success, fail
        while not cw_queue.empty():
            try:
                finding = cw_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            fid = finding.get("finding_id", "?")
            score = finding.get("attention_score", 0)
            label = finding.get("label", "")[:30]
            url = finding.get("url", "")

            site_ctx = {
                "url": url,
                "domain": finding.get("domain", ""),
                "site_name": finding.get("site_name"),
                "entity_name": finding.get("entity_name"),
                "summary": finding.get("summary"),
            }
            siblings = [f for f in by_url.get(url, []) if f.get("finding_id") != fid]

            logger.info(f"[补话术-w{wid}] finding={fid} | score={score} | {label} | 剩余={cw_queue.qsize()}")
            t1 = time.time()
            try:
                cw = await pipeline.generate_copywriting_for_finding(finding, site_ctx, siblings)
                cw["project_id"] = PROJECT_ID
                cw["task_id"] = task_id
                await findings_dao.insert_copywriting(db, {**cw, "source": "web_tagging"})
                success += 1
                logger.info(f"[补话术-w{wid}] ✓ ({time.time()-t1:.1f}s) | {fid}")
            except Exception as e:
                fail += 1
                logger.error(f"[补话术-w{wid}] ✗ ({time.time()-t1:.1f}s) | {fid}: {e}")

    try:
        workers = [asyncio.create_task(_cw_worker(i)) for i in range(NUM_CW_WORKERS)]
        await asyncio.gather(*workers, return_exceptions=True)
        logger.info(f"补话术完成 ({time.time()-t0:.1f}s) | 成功={success} 失败={fail}")
    except Exception as e:
        logger.error(f"补话术异常: {e}", exc_info=True)
    finally:
        tracker.pop_context()


# ═══════════════════════════════════════════
# 测试 3: XHS 画像（清空重跑）
# ═══════════════════════════════════════════

async def test_xhs_profile(app_config, db):
    try:
        await _test_xhs_profile_inner(app_config, db)
    except Exception as e:
        import traceback
        logger.error(f"[XHS-画像] 顶级异常: {e}\n{traceback.format_exc()}")


async def _test_xhs_profile_inner(app_config, db):
    from api.services.xhs_pipeline import XhsPipeline
    from Sere1nGraph.graph.observability import get_global_tracker
    import time as _time

    logger.info("=" * 60)
    logger.info("  测试 3: XHS 画像（清空重跑）+ 话术")
    logger.info("=" * 60)

    # 补数据：xhs_note_details → findings
    await _backfill_xhs_findings(db)

    # 清空该项目的旧画像
    r = await db["xhs_profiles"].delete_many({"project_id": PROJECT_ID})
    logger.info(f"[清理] 删除旧画像 {r.deleted_count} 条")

    # 预览用户数
    cursor = db["findings"].find(
        {"project_id": PROJECT_ID, "source": "xhs", "xhs_user_id": {"$exists": True, "$nin": [None, ""]}, "attention_score": {"$gte": 60}},
        {"xhs_user_id": 1, "attention_score": 1, "value": 1},
    ).sort("attention_score", -1)
    all_f = await cursor.to_list(500)
    seen = set()
    unique = []
    for f in all_f:
        uid = f.get("xhs_user_id")
        if uid and uid not in seen:
            seen.add(uid)
            unique.append(f)
    logger.info(f"待生成画像: {len(unique)} 个用户 (score>=60)")

    if not unique:
        logger.warning("没有可用用户，跳过")
        return

    task_id = f"test_xhs_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    tracker = get_global_tracker()
    tracker.push_context(project_id=PROJECT_ID, task_id=task_id, phase="xhs_profile")

    t0 = _time.time()
    try:
        xhs_pipeline = XhsPipeline(db, app_config)
        profiles = await xhs_pipeline._stage_profile_generation(
            task_id=task_id, project_id=PROJECT_ID,
        )
        logger.info(f"XHS 画像+话术完成 ({_time.time()-t0:.1f}s) | 画像={len(profiles)}")
    except Exception as e:
        logger.error(f"XHS 画像失败 ({_time.time()-t0:.1f}s): {e}", exc_info=True)
    finally:
        tracker.pop_context()


# ═══════════════════════════════════════════
# 补数据
# ═══════════════════════════════════════════

async def _backfill_xhs_findings(db):
    """把 xhs_note_details 中已达标但未入 findings 的记录回填"""
    try:
        import uuid as _uuid
        from api.dao import findings as findings_dao

        cursor = db["xhs_note_details"].find(
            {"project_id": PROJECT_ID, "tagging": {"$exists": True}, "tagging.attention_score": {"$gte": 60}},
            {"_id": 0},
        )
        details = await cursor.to_list(500)

        existing = set(await db["findings"].distinct("note_id", {"project_id": PROJECT_ID, "source": "xhs"}))
        backfilled = 0

        for detail in details:
            note_id = detail.get("note_id")
            if note_id in existing:
                continue
            tagging = detail.get("tagging", {})
            score = tagging.get("attention_score", 0)
            note = await db["xhs_notes"].find_one({"note_id": note_id, "project_id": PROJECT_ID}, {"user": 1, "task_id": 1})
            if not note:
                continue
            user = note.get("user", {})
            uid = user.get("user_id", "")
            nick = user.get("nickname", "")
            tid = note.get("task_id", "")
            if not uid:
                continue
            for df in tagging.get("findings", []):
                await findings_dao.insert_finding(db, {
                    "finding_id": _uuid.uuid4().hex[:12], "project_id": PROJECT_ID, "task_id": tid,
                    "source": "xhs", "type": df.get("type", "other"), "channel": "xhs_note_detail",
                    "label": df.get("value", "")[:80], "value": nick,
                    "url": f"https://www.xiaohongshu.com/explore/{note_id}",
                    "xhs_user_id": uid, "xhs_note_ids": [note_id], "note_id": note_id,
                    "attention_score": score, "attention_reason": df.get("attention_reason", ""),
                    "context": df.get("evidence", ""), "evidence": tagging.get("summary", ""),
                })
                backfilled += 1
            existing.add(note_id)

        logger.info(f"[补数据] 回填 {backfilled} 条 findings")
    except Exception as e:
        logger.error(f"[补数据] 异常: {e}")


# ═══════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════

async def main():
    logger.info("端到端测试启动")
    logger.info(f"日志: {LOG_FILE}")

    app_config, db = init_app()

    from Sere1nGraph.graph.observability import get_global_tracker
    await get_global_tracker().load_history_from_db()

    t0 = time.time()

    # 三个测试并发：URL 扫描用 Docker，画像用 Docker+LLM，补话术纯 LLM
    results = await asyncio.gather(
        test_url_scan(app_config, db),
        test_xhs_profile(app_config, db),
        return_exceptions=True,
    )
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            import traceback
            logger.error(f"测试 {i+1} 异常: {r}\n{''.join(traceback.format_exception(type(r), r, r.__traceback__))}")

    # URL 扫描和画像完成后，补话术
    await test_补话术(app_config, db)

    logger.info(f"\n全部完成 | 总耗时 {time.time()-t0:.1f}s | 日志: {LOG_FILE}")

    try:
        from browser_manager import shutdown_provider
        await shutdown_provider()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
